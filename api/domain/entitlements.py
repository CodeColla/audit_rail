"""Plan entitlements (Phase 10, S2a) — req/phase10/00-issue-19.md, docs/phase10/.

The one place every enforcement point reads a limit or counts usage from; nothing else
should re-derive either. Three guard functions each raise HTTP 402 with a structured
`plan_limit` detail the frontend recognises (webui/src/lib/api.ts, added in S2b):

    {"code": "plan_limit", "limit": "audits"|"storage"|"members", "used": N, "max": N,
     "message": "<human sentence>"}

**Fails OPEN on a missing `tenant_plans` row** — treated as Enterprise/unlimited, with a
warning logged. A missing billing row must never turn into an outage for a legacy tenant
or a test fixture that predates this module; see db/schema.sql's comment on `tenant_plans`.

Several upload routes call `storage.save()` (a disk write) before opening any database
transaction — the check has to happen earlier still, so every guard here accepts
`conn=None` and opens its own short-lived read-only connection in that case.
"""

from __future__ import annotations

import dataclasses

from fastapi import HTTPException
from sqlalchemy import func, select

from api.core.database import engine, t
from api.core.logging import logger
from api.core.util import now_iso
from api.domain.plans import ADDONS, PLANS, PlanCode


@dataclasses.dataclass
class Limits:
    plan_code: PlanCode
    max_active_audits: int | None   # None = unlimited
    storage_bytes: int | None
    max_members: int | None
    in_grace: bool = False          # Pro, payment failed, still inside the grace window
    degraded: bool = False          # was Pro; grace (or the paid period) ran out — now Free


@dataclasses.dataclass
class Usage:
    active_audits: int
    storage_bytes: int
    members: int


def effective_limits(conn, tenant_id: str) -> Limits:
    """Plan + add-on packs + overrides, with grace/lapse already resolved against now."""
    tp = t("tenant_plans")
    row = conn.execute(select(tp).where(tp.c.tenant_id == tenant_id)).mappings().first()
    if row is None:
        logger.warning(f"tenant {tenant_id} has no tenant_plans row — treating as unlimited")
        return Limits(plan_code="enterprise", max_active_audits=None, storage_bytes=None,
                     max_members=None)

    plan_code: PlanCode = row["plan_code"]
    in_grace = degraded = False
    now = now_iso()

    if plan_code == "pro" and row["status"] == "past_due":
        if row["grace_until"] and row["grace_until"] > now:
            in_grace = True
        else:
            degraded, plan_code = True, "free"
    elif (plan_code == "pro" and row["status"] == "cancelled"
          and not (row["current_period_end"] and row["current_period_end"] > now)):
        degraded, plan_code = True, "free"

    plan = PLANS[plan_code]
    max_audits = (row["override_max_active_audits"]
                 if row["override_max_active_audits"] is not None
                 else plan["max_active_audits"])
    storage = (row["override_storage_bytes"]
              if row["override_storage_bytes"] is not None
              else plan["storage_bytes"])
    max_members = (row["override_max_members"]
                  if row["override_max_members"] is not None
                  else plan["max_members"])

    # Add-on packs only ever raise the PRO plan's own numeric limits — Free has nothing to
    # buy a pack for, and Enterprise/unlimited has no ceiling for a pack to raise.
    if plan_code == "pro":
        if max_audits is not None:
            max_audits += row["addon_audit_packs"] * ADDONS["audits"]["audits"]
        if storage is not None:
            storage += row["addon_storage_packs"] * ADDONS["storage"]["storage_bytes"]

    return Limits(plan_code=plan_code, max_active_audits=max_audits, storage_bytes=storage,
                 max_members=max_members, in_grace=in_grace, degraded=degraded)


def usage(conn, tenant_id: str) -> Usage:
    a, f, m = t("assessments"), t("files"), t("tenant_members")
    active_audits = conn.execute(
        select(func.count()).select_from(a)
        .where(a.c.tenant_id == tenant_id, a.c.status != "closed")
    ).scalar() or 0
    storage_bytes = conn.execute(
        select(func.coalesce(func.sum(f.c.size_bytes), 0)).where(f.c.tenant_id == tenant_id)
    ).scalar() or 0
    members = conn.execute(
        select(func.count()).select_from(m).where(m.c.tenant_id == tenant_id)
    ).scalar() or 0
    return Usage(active_audits=active_audits, storage_bytes=storage_bytes, members=members)


def _limit_error(limit: str, used: int, max_: int, message: str) -> HTTPException:
    return HTTPException(402, {"code": "plan_limit", "limit": limit, "used": used,
                               "max": max_, "message": message})


def ensure_can_open_audit(conn, tenant_id: str) -> None:
    """Call before creating an audit AND before reopening a closed one — a closed audit
    going back to any non-closed status is new growth exactly like creating one, and a
    checklist import is always followed by creating the audit it was imported for (see
    templates.import_checklist), so it is checked here too."""
    if conn is None:
        with engine.connect() as c:
            return ensure_can_open_audit(c, tenant_id)
    limits = effective_limits(conn, tenant_id)
    if limits.max_active_audits is None:
        return
    used = usage(conn, tenant_id).active_audits
    if used >= limits.max_active_audits:
        n = limits.max_active_audits
        raise _limit_error(
            "audits", used, n,
            f"Your {PLANS[limits.plan_code]['name']} plan allows {n} active audit"
            f"{'' if n == 1 else 's'} at a time. Close one, or upgrade to open another.")


def ensure_storage(conn, tenant_id: str, incoming_bytes: int) -> None:
    """Call before writing to the vault (storage.save), with the SIZE of the bytes about to
    be written. Never applied to system-generated files (a published document's rendered
    PDF) — those count toward usage once written, but publishing must not break over a
    limit the user did not cause and cannot see."""
    if conn is None:
        with engine.connect() as c:
            return ensure_storage(c, tenant_id, incoming_bytes)
    limits = effective_limits(conn, tenant_id)
    if limits.storage_bytes is None:
        return
    used = usage(conn, tenant_id).storage_bytes
    if used + incoming_bytes > limits.storage_bytes:
        raise _limit_error(
            "storage", used, limits.storage_bytes,
            f"This upload would put you over your {PLANS[limits.plan_code]['name']} plan's "
            f"storage limit. Free up space, or upgrade for more room.")


def ensure_can_add_member(conn, tenant_id: str) -> None:
    """Call before a login is added to the tenant — `tenant_members`, not `people`. Staff
    who only attest by magic link are never counted."""
    if conn is None:
        with engine.connect() as c:
            return ensure_can_add_member(c, tenant_id)
    limits = effective_limits(conn, tenant_id)
    if limits.max_members is None:
        return
    used = usage(conn, tenant_id).members
    if used >= limits.max_members:
        raise _limit_error(
            "members", used, limits.max_members,
            f"Your {PLANS[limits.plan_code]['name']} plan allows {limits.max_members} "
            f"logins. Remove one, or upgrade for more.")
