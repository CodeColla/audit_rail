"""Razorpay billing logic (Phase 10, S3a) — req/phase10/00-issue-19.md,
docs/phase10/01-sprint-plan.md.

Signature verification, the webhook's effect on a subscription row, and recomputing
`tenant_plans` from whatever `razorpay_subscriptions` rows a tenant currently has. This
module never talks to Razorpay directly — that's `api/domain/razorpay_client.py` — and
never decides what a tenant may DO — that's `api/domain/entitlements.py`, which just
reads whatever `sync_tenant_plan` leaves in `tenant_plans`, exactly as it already does for
a manually-set Enterprise deal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac

from sqlalchemy import select, update

from api.core.config import settings
from api.core.database import t
from api.core.logging import logger
from api.core.util import now_iso, now_plus_days

#: A Pro/add-on subscription counts as "live" in either of these states — `authenticated`
#: is what a successful checkout leaves it in (api/routers/billing.py's /verify), before
#: the first actual charge has necessarily landed; `active` is what the webhook confirms
#: once it has.
_LIVE_STATUSES = ("authenticated", "active")
_LAPSED_STATUSES = ("pending", "halted")
_ENDED_STATUSES = ("cancelled", "completed", "expired")


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256, hex, over the RAW body — verify before any JSON parsing. Keyed with
    the webhook secret set in the Razorpay dashboard, which is a value chosen there, NOT
    derived from razorpay_key_secret (confirmed against Razorpay's own docs — see
    docs/phase10/01-sprint-plan.md's S3a spike notes)."""
    if not settings.razorpay_webhook_secret or not signature:
        return False
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw_body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_checkout(payment_id: str, subscription_id: str, signature: str) -> bool:
    """HMAC-SHA256(payment_id + "|" + subscription_id, KEY SECRET) — confirmed against the
    real test API and Razorpay's own subscriptions integration guide."""
    if not settings.razorpay_key_secret or not signature:
        return False
    expected = hmac.new(settings.razorpay_key_secret.encode(),
                        f"{payment_id}|{subscription_id}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _unix_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_event(conn, payload: dict) -> None:
    """The effect of one webhook delivery. Called only after the signature has verified
    AND the event has already been recorded for idempotency (api/routers/billing.py) —
    this function itself has no replay protection of its own.

    `payload["subscription"]["entity"]` — not `payload["subscription"]` — is where
    Razorpay actually nests the subscription object; easy to miss, confirmed against
    Razorpay's own documented shape (docs/phase10/01-sprint-plan.md's S3a spike notes).
    """
    sub_entity = (payload.get("payload") or {}).get("subscription", {}).get("entity")
    if sub_entity is None:
        logger.info(f"billing event {payload.get('event')!r} carries no subscription — "
                   f"nothing to apply")
        return

    sub_id = sub_entity.get("id")
    rs = t("razorpay_subscriptions")
    row = conn.execute(select(rs).where(rs.c.id == sub_id)).mappings().first()
    if row is None:
        # Not one of OUR subscriptions (a different Razorpay product on the same
        # account, or a sandbox artifact) — logged, not raised, so the webhook route
        # still answers 200 and Razorpay stops retrying it.
        logger.warning(f"webhook for unrecognised subscription {sub_id!r} — ignoring")
        return

    conn.execute(update(rs).where(rs.c.id == sub_id).values(
        status=sub_entity.get("status", row["status"]),
        quantity=sub_entity.get("quantity", row["quantity"]),
        current_end=_unix_to_iso(sub_entity.get("current_end")) or row["current_end"],
        updated_at=now_iso()))
    sync_tenant_plan(conn, row["tenant_id"])


def sync_tenant_plan(conn, tenant_id: str) -> None:
    """Recomputes `tenant_plans` from whatever `razorpay_subscriptions` rows this tenant
    currently has — the only thing that ever writes plan_code/status/grace_until for a
    Public tenant once Razorpay is involved. Called after `apply_event` and, eagerly, by
    `/billing/verify` right after a successful checkout (so the UI shows Pro immediately,
    without waiting on the webhook — the webhook still runs and reconfirms the same state
    once it arrives).
    """
    tp = t("tenant_plans")
    current = conn.execute(select(tp).where(tp.c.tenant_id == tenant_id)).mappings().first()
    if current is None:
        logger.warning(f"sync_tenant_plan: tenant {tenant_id} has no tenant_plans row")
        return
    if current["plan_code"] == "enterprise":
        return  # a sales-negotiated deal (scripts/set_plan.py) — Razorpay never touches it

    rs = t("razorpay_subscriptions")
    rows = conn.execute(select(rs).where(rs.c.tenant_id == tenant_id)).mappings().all()

    addon_audit_packs = sum(r["quantity"] for r in rows
                            if r["item"] == "addon_audits" and r["status"] in _LIVE_STATUSES)
    addon_storage_packs = sum(r["quantity"] for r in rows
                              if r["item"] == "addon_storage" and r["status"] in _LIVE_STATUSES)
    vals: dict = {
        "addon_audit_packs": addon_audit_packs,
        "addon_storage_packs": addon_storage_packs,
        "updated_at": now_iso(),
    }

    pro_rows = [r for r in rows if r["item"] == "pro"]
    # In practice there's at most one — /billing/checkout 409s while one is already live
    # — but pick the most recently updated if that invariant is ever violated.
    pro = max(pro_rows, key=lambda r: r["updated_at"], default=None)

    if pro is not None and pro["status"] in _LIVE_STATUSES:
        vals.update(plan_code="pro", status="active", billing_cycle=pro["billing_cycle"],
                   current_period_end=pro["current_end"], grace_until=None)
    elif pro is not None and pro["status"] in _LAPSED_STATUSES:
        vals.update(plan_code="pro", status="past_due", billing_cycle=pro["billing_cycle"],
                   current_period_end=pro["current_end"])
        if not current["grace_until"]:  # set ONCE per past_due episode, not re-extended
            vals["grace_until"] = now_plus_days(settings.billing_grace_days)
    elif pro is not None and pro["status"] in _ENDED_STATUSES:
        vals.update(plan_code="pro", status="cancelled", billing_cycle=pro["billing_cycle"],
                   current_period_end=pro["current_end"])
        # entitlements.py's existing Public branch already treats a cancelled Pro past its
        # own current_period_end as Free — nothing more to do here.
    # else: `pro` is None, or still `created` (checkout started, never completed) — no
    # plan change, but the add-on counts above still need writing.

    conn.execute(update(tp).where(tp.c.tenant_id == tenant_id).values(**vals))
    logger.info(f"tenant {tenant_id} synced: plan_code={vals.get('plan_code', current['plan_code'])}, "
               f"addons=({addon_audit_packs},{addon_storage_packs})")
