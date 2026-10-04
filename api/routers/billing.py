"""Billing (Phase 10, S2a + S3a) — req/phase10/00-issue-19.md, docs/phase10/.

Two deployment modes, one module, two routers. In `"public"` mode (the shared,
multi-tenant SaaS): plan, limits and usage from `tenant_plans`, Pro sold through
Razorpay **Subscriptions** (not one-time Orders — Pro is a recurring monthly/yearly
charge). In `"enterprise"` mode (a standalone, single-tenant deployment, docs/phase10/
03-enterprise-licensing.md): a signed license token instead of a gateway.

Either way, `entitlements.py` is the only thing that decides what a tenant may do; this
module only reports it and, for Public, drives Razorpay/lets a license be applied.

`router` carries every authenticated endpoint. `public_router` carries exactly one route
— the Razorpay webhook — and is registered separately in `api/main.py`, next to
`signing.router`, because it has no `Depends(get_current_user)` at all: Razorpay calls it
directly, and the HMAC signature on the raw body is the only thing that authenticates it.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.core import activity
from api.core.auth import Principal, get_current_user
from api.core.config import settings
from api.core.database import engine, get_conn, t
from api.core.logging import logger
from api.core.permissions import require
from api.core.util import StrictModel, now_iso
from api.domain import billing, entitlements, licensing, razorpay_client
from api.domain.plans import ADDONS, PLANS

router = APIRouter(tags=["billing"])
public_router = APIRouter(prefix="/billing", tags=["billing-webhook"])

#: A subscription in any of these Razorpay statuses still represents real, ongoing intent
#: — "has the org already started or completed buying Pro" needs all of them, not just
#: the ones that have actually started charging.
_NOT_ENDED = ("created", "authenticated", "active", "pending", "halted")


def _require_public_billing() -> None:
    if settings.deployment_mode != "public":
        raise HTTPException(400, "this deployment does not use self-serve billing")
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(503, "billing is not configured on this deployment")


def _plan_id_for(item: str, cycle: str) -> str:
    plan_id = {
        ("pro", "monthly"): settings.razorpay_plan_pro_monthly,
        ("pro", "yearly"): settings.razorpay_plan_pro_yearly,
        ("addon_audits", "monthly"): settings.razorpay_plan_addon_audits_monthly,
        ("addon_audits", "yearly"): settings.razorpay_plan_addon_audits_yearly,
        ("addon_storage", "monthly"): settings.razorpay_plan_addon_storage_monthly,
        ("addon_storage", "yearly"): settings.razorpay_plan_addon_storage_yearly,
    }[(item, cycle)]
    if not plan_id:
        raise HTTPException(503, f"no Razorpay plan is configured for {item}/{cycle}")
    return plan_id


def _total_count(cycle: str) -> int:
    return 120 if cycle == "monthly" else 10


def _create_subscription_row(conn, *, tenant_id: str, item: str, cycle: str,
                             quantity: int, created_by_user_id: str) -> dict:
    """The one place that both calls Razorpay to create a subscription AND inserts the
    row for it — checkout and the add-ons endpoint both need exactly this, for a brand
    new Pro purchase or a brand new/recreated add-on pack alike."""
    try:
        sub = razorpay_client.create_subscription(
            plan_id=_plan_id_for(item, cycle), quantity=quantity,
            total_count=_total_count(cycle), notes={"tenant_id": tenant_id, "item": item})
    except razorpay_client.RazorpayError as e:
        logger.error(f"could not create {item} subscription for tenant {tenant_id}: {e}")
        raise HTTPException(500, "could not start checkout with the payment provider — "
                                 "try again in a moment")
    now = now_iso()
    conn.execute(insert(t("razorpay_subscriptions")).values(
        id=sub["id"], tenant_id=tenant_id, item=item, billing_cycle=cycle,
        quantity=quantity, status=sub.get("status", "created"), current_end=None,
        short_url=sub.get("short_url"), created_by_user_id=created_by_user_id,
        created_at=now, updated_at=now))
    return {"id": sub["id"], "item": item, "short_url": sub.get("short_url"),
           "needs_checkout": True}


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


# ──────────────────────────────────────────────────────────────── Razorpay (Public mode)

class CheckoutIn(StrictModel):
    cycle: str  # "monthly" | "yearly"
    addon_audit_packs: int = 0
    addon_storage_packs: int = 0


@router.post("/billing/checkout", status_code=201)
def checkout(body: CheckoutIn, user: Principal = Depends(require("billing", "edit"))):
    if body.cycle not in ("monthly", "yearly"):
        raise HTTPException(400, "cycle must be 'monthly' or 'yearly'")
    _require_public_billing()

    rs = t("razorpay_subscriptions")
    with engine.begin() as conn:
        live_pro = conn.execute(select(rs.c.id).where(
            rs.c.tenant_id == user.tenant_id, rs.c.item == "pro",
            rs.c.status.in_(_NOT_ENDED))).first()
        if live_pro:
            raise HTTPException(409, "this organisation already has a Pro subscription "
                                     "in progress or active")

        items = [("pro", 1)]
        if body.addon_audit_packs > 0:
            items.append(("addon_audits", body.addon_audit_packs))
        if body.addon_storage_packs > 0:
            items.append(("addon_storage", body.addon_storage_packs))

        created = [_create_subscription_row(conn, tenant_id=user.tenant_id, item=item,
                                            cycle=body.cycle, quantity=qty,
                                            created_by_user_id=user.user_id)
                  for item, qty in items]

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="billing.checkout_started", entity_type="tenant",
                 entity_id=user.tenant_id,
                 detail={"cycle": body.cycle, "items": [c["item"] for c in created]})
    return {"key_id": settings.razorpay_key_id, "subscriptions": created,
           "prefill": {"name": user["full_name"], "email": user["email"]}}


class VerifyIn(StrictModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


@router.post("/billing/verify")
def verify(body: VerifyIn, user: Principal = Depends(require("billing", "edit"))):
    """Marks a subscription `authenticated` right after a successful checkout, so the UI
    shows Pro immediately rather than waiting on the webhook — which still arrives
    separately and reconfirms (or corrects) the same state. Never trusted on its own
    without the signature check: an unsigned browser callback proves nothing."""
    _require_public_billing()
    if not billing.verify_checkout(body.razorpay_payment_id, body.razorpay_subscription_id,
                                   body.razorpay_signature):
        raise HTTPException(400, "payment signature could not be verified")

    rs = t("razorpay_subscriptions")
    with engine.begin() as conn:
        row = conn.execute(select(rs).where(
            rs.c.id == body.razorpay_subscription_id,
            rs.c.tenant_id == user.tenant_id)).mappings().first()
        if row is None:
            raise HTTPException(404, "subscription not found for this organisation")
        conn.execute(update(rs).where(rs.c.id == row["id"]).values(
            status="authenticated", updated_at=now_iso()))
        billing.sync_tenant_plan(conn, user.tenant_id)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="billing.activated", entity_type="tenant", entity_id=user.tenant_id,
                 detail={"subscription_id": row["id"], "item": row["item"]})
    return {"ok": True}


class AddonsIn(StrictModel):
    addon_audit_packs: int | None = None
    addon_storage_packs: int | None = None


@router.post("/billing/addons")
def change_addons(body: AddonsIn, user: Principal = Depends(require("billing", "edit"))):
    _require_public_billing()
    rs = t("razorpay_subscriptions")
    changes: list[dict] = []

    with engine.begin() as conn:
        pro = conn.execute(select(rs).where(
            rs.c.tenant_id == user.tenant_id, rs.c.item == "pro",
            rs.c.status.in_(("authenticated", "active")))).mappings().first()
        if pro is None:
            raise HTTPException(409, "upgrade to Pro before buying add-ons")
        cycle = pro["billing_cycle"]

        for item, new_qty in (("addon_audits", body.addon_audit_packs),
                              ("addon_storage", body.addon_storage_packs)):
            if new_qty is None:
                continue
            if new_qty < 0:
                raise HTTPException(400, "pack count can't be negative")

            existing = conn.execute(select(rs).where(
                rs.c.tenant_id == user.tenant_id, rs.c.item == item,
                rs.c.status.in_(_NOT_ENDED))).mappings().first()

            if new_qty == 0:
                if existing:
                    razorpay_client.cancel_subscription(existing["id"])
                    conn.execute(update(rs).where(rs.c.id == existing["id"]).values(
                        status="cancelled", updated_at=now_iso()))
                    changes.append({"item": item, "action": "cancelled"})
                continue

            if existing is None:
                changes.append(_create_subscription_row(
                    conn, tenant_id=user.tenant_id, item=item, cycle=cycle,
                    quantity=new_qty, created_by_user_id=user.user_id)
                    | {"action": "created"})
            elif existing["status"] in ("authenticated", "active"):
                # Confirmed against the real test API: quantity CAN be changed once a
                # subscription has actually been authenticated — see
                # tests/fixtures/razorpay/subscription_quantity_update_rejected.json.
                razorpay_client.update_subscription_quantity(existing["id"], new_qty)
                conn.execute(update(rs).where(rs.c.id == existing["id"]).values(
                    quantity=new_qty, updated_at=now_iso()))
                changes.append({"item": item, "action": "updated", "needs_checkout": False})
            else:
                # Still "created"/"pending"/"halted" — never successfully authenticated,
                # so Razorpay rejects a quantity change outright (confirmed by the spike).
                # Cancel it and start a fresh one at the new quantity.
                razorpay_client.cancel_subscription(existing["id"])
                conn.execute(update(rs).where(rs.c.id == existing["id"]).values(
                    status="cancelled", updated_at=now_iso()))
                changes.append(_create_subscription_row(
                    conn, tenant_id=user.tenant_id, item=item, cycle=cycle,
                    quantity=new_qty, created_by_user_id=user.user_id)
                    | {"action": "recreated"})

        billing.sync_tenant_plan(conn, user.tenant_id)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="billing.addons_changed", entity_type="tenant",
                 entity_id=user.tenant_id, detail={"changes": changes})
    return {"ok": True, "subscriptions": changes, "key_id": settings.razorpay_key_id,
           "prefill": {"name": user["full_name"], "email": user["email"]}}


@router.post("/billing/cancel")
def cancel(user: Principal = Depends(require("billing", "edit"))):
    """`cancel_at_cycle_end` on every live subscription — access runs out at the end of
    the period already paid for, same promise as the Refund & Cancellation policy
    (req/phase10/00-issue-19.md §1). Razorpay doesn't flip the subscription's status to
    `cancelled` until the cycle actually ends, so `tenant_plans` isn't touched here
    either — the eventual `subscription.cancelled` webhook is what does that, through the
    normal apply_event()/sync_tenant_plan() path."""
    _require_public_billing()
    rs = t("razorpay_subscriptions")
    with engine.begin() as conn:
        live = conn.execute(select(rs).where(
            rs.c.tenant_id == user.tenant_id,
            rs.c.status.in_(("authenticated", "active", "pending", "halted")))).mappings().all()
        if not live:
            raise HTTPException(404, "no active subscription to cancel")
        for row in live:
            try:
                razorpay_client.cancel_subscription(row["id"], at_cycle_end=True)
            except razorpay_client.RazorpayError as e:
                logger.error(f"could not cancel subscription {row['id']}: {e}")
                raise HTTPException(500, "could not reach the payment provider — try again")
            conn.execute(update(rs).where(rs.c.id == row["id"]).values(updated_at=now_iso()))

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="billing.cancelled", entity_type="tenant", entity_id=user.tenant_id,
                 detail={"subscriptions": [r["id"] for r in live]})
    return {"ok": True}


@router.get("/billing/invoices")
def list_invoices(user: Principal = Depends(require("billing", "view")), conn=Depends(get_conn)):
    _require_public_billing()
    rs = t("razorpay_subscriptions")
    sub_ids = conn.execute(select(rs.c.id).where(rs.c.tenant_id == user.tenant_id)).scalars().all()

    out = []
    for sub_id in sub_ids:
        try:
            invoices = razorpay_client.list_invoices(sub_id)
        except razorpay_client.RazorpayError as e:
            logger.warning(f"could not list invoices for {sub_id}: {e}")
            continue  # best-effort — one bad lookup shouldn't blank the whole page
        for inv in invoices:
            paid_at = inv.get("paid_at") or inv.get("created_at")
            out.append({
                "date": (dt.datetime.fromtimestamp(paid_at, tz=dt.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ") if paid_at else None),
                "amount": inv.get("amount"),
                "status": inv.get("status"),
                "short_url": inv.get("short_url"),
            })
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return {"invoices": out}


@public_router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    """PUBLIC, unauthenticated — see this module's own docstring for why. Always answers
    200 once the signature has checked out, even for an event we don't recognise or don't
    act on, because anything else makes Razorpay retry forever; a genuine processing
    failure is the one case that SHOULD get a 500, so it does retry."""
    if settings.deployment_mode != "public":
        return {"ok": True}  # nothing to do here; still 200, never 404/400 Razorpay would retry

    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not billing.verify_webhook(raw, signature):
        logger.warning("razorpay webhook: signature verification failed")
        raise HTTPException(400, "invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON body")

    event_id = request.headers.get("X-Razorpay-Event-Id")
    if not event_id:
        # Not expected in practice — Razorpay always sends this — but a webhook with no
        # idempotency key at all must still not be processed twice, so fall back to a
        # deterministic key over the body it actually received.
        import hashlib
        event_id = f"nokey_{hashlib.sha256(raw).hexdigest()}"

    sub_entity = (payload.get("payload") or {}).get("subscription", {}).get("entity") or {}
    tenant_id = (sub_entity.get("notes") or {}).get("tenant_id")

    be = t("billing_events")
    try:
        with engine.begin() as conn:
            stmt = pg_insert(be).values(
                razorpay_event_id=event_id, event_type=payload.get("event", ""),
                tenant_id=tenant_id, payload=payload, received_at=now_iso(),
            ).on_conflict_do_nothing(index_elements=["razorpay_event_id"])
            inserted = conn.execute(stmt.returning(be.c.razorpay_event_id)).first()
            if inserted is None:
                logger.info(f"razorpay webhook {event_id} already processed — no-op")
                return {"ok": True}
            billing.apply_event(conn, payload)
            conn.execute(update(be).where(be.c.razorpay_event_id == event_id)
                        .values(processed_at=now_iso()))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — a 500 here makes Razorpay retry, which is the point
        logger.error(f"razorpay webhook {event_id} processing failed: {e}")
        raise HTTPException(500, "something went wrong processing this event")

    return {"ok": True}


# ──────────────────────────────────────────────────────────── Enterprise licensing

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
