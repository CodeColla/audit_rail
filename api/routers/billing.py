"""Billing (Phase 10, S2a) — req/phase10/00-issue-19.md, docs/phase10/.

Two deployment modes, one router. In `"public"` mode (the shared, multi-tenant SaaS):
plan, limits and usage from `tenant_plans` — read-only this sprint; Razorpay checkout,
add-ons, cancel and invoices land in S3a/S3b (docs/phase10/01-sprint-plan.md). In
`"enterprise"` mode (a standalone, single-tenant deployment, docs/phase10/
03-enterprise-licensing.md): a signed license token instead of a gateway — `GET /billing`
reports its status, `PUT /license` is how a Super Admin applies a new one.

Either way, `entitlements.py` is the only thing that decides what a tenant may do; this
router only reports it (and, for a license, lets it be changed).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, select

from api.core.auth import Principal, get_current_user
from api.core.config import settings
from api.core.database import engine, get_conn, t
from api.core.logging import logger
from api.core.permissions import require
from api.core.util import StrictModel, now_iso
from api.domain import entitlements, licensing
from api.domain.plans import ADDONS, PLANS

router = APIRouter(tags=["billing"])


@router.get("/billing/plans")
def list_plans(user: Principal = Depends(get_current_user)):
    """The prices and limits the UI shows — any logged-in member, not just an admin, since
    a non-admin still needs to see what upgrading would mean before asking their admin to.
    Meaningless in enterprise mode (there's nothing to self-serve buy); returned anyway so
    the frontend never has to special-case a 404 on a call it doesn't make there."""
    return {"plans": PLANS, "addons": ADDONS}


@router.get("/billing")
def get_billing(user: Principal = Depends(require("billing", "view")),
               conn=Depends(get_conn)):
    limits = entitlements.effective_limits(conn, user.tenant_id)
    used = entitlements.usage(conn, user.tenant_id)
    body = {
        "deployment_mode": settings.deployment_mode,
        "plan_code": limits.plan_code,
        "plan_name": PLANS[limits.plan_code]["name"],
        "in_grace": limits.in_grace,
        "degraded": limits.degraded,
        "limits": {
            "max_active_audits": limits.max_active_audits,
            "storage_bytes": limits.storage_bytes,
            "max_members": limits.max_members,
        },
        "usage": {
            "active_audits": used.active_audits,
            "storage_bytes": used.storage_bytes,
            "members": used.members,
        },
    }

    if settings.deployment_mode == "enterprise":
        dl = t("deployment_license")
        row = conn.execute(select(dl).where(dl.c.id == 1)).mappings().first()
        lic = licensing.decode(row["token"]) if row else None
        if limits.license_issue:
            status = {"missing": "frozen_missing", "invalid": "frozen_invalid",
                      "expired": "frozen_expired"}[limits.license_issue]
        elif limits.in_grace:
            status = "grace"                 # expired, still within its post-expiry grace
        elif row is None:
            status = "unlicensed"            # within the initial onboarding grace, nothing applied yet
        elif lic is None:
            status = "unverified"            # applied, but fails to decode — within its own grace
        else:
            status = "active"
        body["license"] = {
            "status": status,
            "customer": lic.customer if lic else None,
            "issued_at": lic.issued_at if lic else None,
            "expires_at": lic.expires_at if lic else None,
            "applied_at": row["applied_at"] if row else None,
        }
    else:
        tp = t("tenant_plans")
        row = conn.execute(select(tp).where(tp.c.tenant_id == user.tenant_id)).mappings().first()
        body["billing_cycle"] = row["billing_cycle"] if row else None
        body["status"] = row["status"] if row else "active"
        body["current_period_end"] = row["current_period_end"] if row else None
        body["grace_until"] = row["grace_until"] if row else None
        body["addon_audit_packs"] = row["addon_audit_packs"] if row else 0
        body["addon_storage_packs"] = row["addon_storage_packs"] if row else 0

    return body


class LicenseIn(StrictModel):
    token: str


@router.put("/license", status_code=201)
def apply_license(body: LicenseIn, user: Principal = Depends(require("billing", "edit"))):
    """Apply a new or renewed license token. Rejects a token that doesn't verify — wrong
    signature, wrong issuer, malformed claims — outright, and never stores it; does NOT
    reject an already-expired-but-genuine token, since the grace/freeze state machine in
    entitlements.py is what's meant to handle that, not this endpoint."""
    if settings.deployment_mode != "enterprise":
        raise HTTPException(400, "this deployment does not use licensing")
    lic = licensing.decode(body.token)
    if lic is None:
        raise HTTPException(400, "that license token could not be verified — check it was "
                                 "copied in full, with nothing added or missing")
    dl = t("deployment_license")
    with engine.begin() as conn:
        exists = conn.execute(select(dl.c.id).where(dl.c.id == 1)).first()
        vals = dict(token=body.token, applied_at=now_iso(), applied_by_user_id=user.user_id)
        if exists:
            conn.execute(dl.update().where(dl.c.id == 1).values(**vals))
        else:
            conn.execute(insert(dl).values(id=1, **vals))
    logger.info(f"license applied for {lic.customer!r}: {lic.max_members} members, "
               f"{lic.storage_bytes} bytes, expires {lic.expires_at}")
    return {"ok": True, "customer": lic.customer, "expires_at": lic.expires_at}
