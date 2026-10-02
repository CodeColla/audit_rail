"""Billing (Phase 10, S2a) — req/phase10/00-issue-19.md, docs/phase10/.

Read-only this sprint: plan, limits and usage. Razorpay checkout, add-ons, cancel and
invoices land in S3a/S3b — see docs/phase10/01-sprint-plan.md. Nothing here changes a
plan; `entitlements.py` is the only thing that decides what a tenant may do, this just
reports it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from api.core.auth import Principal, get_current_user
from api.core.database import get_conn, t
from api.core.permissions import require
from api.domain import entitlements
from api.domain.plans import ADDONS, PLANS

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
def list_plans(user: Principal = Depends(get_current_user)):
    """The prices and limits the UI shows — any logged-in member, not just an admin, since
    a non-admin still needs to see what upgrading would mean before asking their admin to."""
    return {"plans": PLANS, "addons": ADDONS}


@router.get("")
def get_billing(user: Principal = Depends(require("billing", "view")),
               conn=Depends(get_conn)):
    limits = entitlements.effective_limits(conn, user.tenant_id)
    used = entitlements.usage(conn, user.tenant_id)
    tp = t("tenant_plans")
    row = conn.execute(select(tp).where(tp.c.tenant_id == user.tenant_id)).mappings().first()
    return {
        "plan_code": limits.plan_code,
        "plan_name": PLANS[limits.plan_code]["name"],
        "in_grace": limits.in_grace,
        "degraded": limits.degraded,
        "billing_cycle": row["billing_cycle"] if row else None,
        "status": row["status"] if row else "active",
        "current_period_end": row["current_period_end"] if row else None,
        "grace_until": row["grace_until"] if row else None,
        "addon_audit_packs": row["addon_audit_packs"] if row else 0,
        "addon_storage_packs": row["addon_storage_packs"] if row else 0,
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
