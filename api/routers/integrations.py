"""External integration ingestion & token lifecycle (P8-S0).

Distinct from every other router: the ingestion endpoints added in later Phase-8 sprints
(asset heartbeat, compliance-config checks) are called by machines — agents, scripts — not
the SPA, and authed via a long-lived `integration_tokens` bearer token
(`api.core.service_auth.get_integration_principal`), not a member JWT. Token *lifecycle*
(issue/list/revoke) is the opposite: a human does that from the SPA, so those endpoints stay
on the normal JWT + `require()` RBAC path.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import insert, select, update

from api.core.activity import log as activity_log
from api.core.auth import Principal
from api.core.database import engine, t
from api.core.logging import logger
from api.core.permissions import require
from api.core.service_auth import (IntegrationPrincipal, exchange_install_token,
                                   get_integration_principal, issue_install_token)
from api.core.util import IsoDate, StrictModel, now_iso

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CHECK_STATUSES = ("PASS", "FAIL", "UNKNOWN")

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _member_id(conn, tenant_id, user_id):
    m = t("tenant_members")
    return conn.execute(select(m.c.id).where(
        m.c.tenant_id == tenant_id, m.c.user_id == user_id)).scalar()


# ── token lifecycle — JWT-authed, a human manages these ────────────────────────────────

class IssueInstallTokenIn(StrictModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/tokens/install")
def create_install_token(body: IssueInstallTokenIn,
                         user: Principal = Depends(require("assets", "edit"))):
    """Issue a 24h install token for a human to hand to a new agent/integration."""
    try:
        with engine.begin() as conn:
            raw = issue_install_token(conn, tenant_id=user.tenant_id, name=body.name,
                                      member_id=_member_id(conn, user.tenant_id, user.user_id))
        activity_log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                    action="create", entity_type="integration_token", detail={"name": body.name})
        return {"token": raw, "expires_in_hours": 24}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_install_token failed: {e}")
        raise HTTPException(500, "something went wrong — try again")


class ExchangeIn(StrictModel):
    install_token: str


@router.post("/tokens/exchange")
def exchange_token(body: ExchangeIn):
    """No JWT — the install token itself is the credential (same discipline as /sign)."""
    raw = exchange_install_token(body.install_token)
    return {"token": raw}


@router.get("/tokens")
def list_tokens(user: Principal = Depends(require("assets", "view"))):
    it = t("integration_tokens")
    with engine.connect() as conn:
        rows = conn.execute(
            select(it.c.id, it.c.name, it.c.kind, it.c.created_at, it.c.expires_at,
                  it.c.last_used_at, it.c.revoked_at)
            .where(it.c.tenant_id == user.tenant_id)
            .order_by(it.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, user: Principal = Depends(require("assets", "edit"))):
    it = t("integration_tokens")
    try:
        with engine.begin() as conn:
            row = conn.execute(select(it.c.id).where(
                it.c.id == token_id, it.c.tenant_id == user.tenant_id)).first()
            if row is None:
                raise HTTPException(404, "Token not found")
            conn.execute(update(it).where(it.c.id == token_id).values(revoked_at=now_iso()))
        activity_log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                    action="revoke", entity_type="integration_token", entity_id=token_id)
        return {"revoked": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"revoke_token failed for {token_id}: {e}")
        raise HTTPException(500, "something went wrong — try again")


# ── ingestion — long-lived-token-authed, machines call these (P8-S1+) ──────────────────

class HeartbeatIn(StrictModel):
    asset_id: str
    checked_at: IsoDate = None  # defaults to server time if omitted


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn,
             principal: IntegrationPrincipal = Depends(get_integration_principal)):
    """An agent reports 'this asset is still around.' Updates `assets.last_seen_at` — never
    trusts a client-supplied tenant, only what the token itself resolved to."""
    a = t("assets")
    try:
        with engine.begin() as conn:
            row = conn.execute(select(a.c.id).where(
                a.c.id == body.asset_id, a.c.tenant_id == principal.tenant_id)).first()
            if row is None:
                raise HTTPException(404, "asset not found")
            conn.execute(update(a).where(a.c.id == body.asset_id).values(
                last_seen_at=body.checked_at or now_iso()))
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"heartbeat failed for {body.asset_id}: {e}")
        raise HTTPException(500, "something went wrong — try again")


# ── alert ingestion (P8-S2) ─────────────────────────────────────────────────────────────
# Staged for human review, NOT written into `findings` directly — see the schema comment on
# `ingested_alerts` for why. A human promotes one to a real finding by hand, through the
# existing per-assessment findings flow, if it's audit-worthy.

class AlertIn(StrictModel):
    asset_id: str | None = None
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    severity: str | None = None
    occurred_at: IsoDate = None          # defaults to server time if omitted
    source_event_id: str | None = None   # the external system's own id, for dedup on re-delivery


@router.post("/alerts", status_code=201)
def ingest_alert(body: AlertIn,
                 principal: IntegrationPrincipal = Depends(get_integration_principal)):
    if body.severity and body.severity not in SEVERITIES:
        raise HTTPException(400, f"severity must be one of {', '.join(SEVERITIES)}")
    ia, a = t("ingested_alerts"), t("assets")
    try:
        with engine.begin() as conn:
            if body.asset_id is not None:
                row = conn.execute(select(a.c.id).where(
                    a.c.id == body.asset_id, a.c.tenant_id == principal.tenant_id)).first()
                if row is None:
                    raise HTTPException(404, "asset not found")
            if body.source_event_id:
                existing = conn.execute(select(ia.c.id).where(
                    ia.c.tenant_id == principal.tenant_id, ia.c.source == principal.token_name,
                    ia.c.source_event_id == body.source_event_id)).first()
                if existing:
                    return {"id": existing[0], "deduplicated": True}
            aid = str(uuid.uuid4())
            conn.execute(insert(ia).values(
                id=aid, tenant_id=principal.tenant_id, asset_id=body.asset_id,
                source=principal.token_name, source_event_id=body.source_event_id,
                title=body.title, description=body.description, severity=body.severity,
                occurred_at=body.occurred_at or now_iso(), status="new", created_at=now_iso()))
        return {"id": aid, "deduplicated": False}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ingest_alert failed: {e}")
        raise HTTPException(500, "something went wrong — try again")


@router.get("/alerts")
def list_alerts(status: str | None = Query(None), asset_id: str | None = Query(None),
                user: Principal = Depends(require("assets", "view"))):
    ia = t("ingested_alerts")
    stmt = (select(ia).where(ia.c.tenant_id == user.tenant_id)
           .order_by(ia.c.occurred_at.desc()))
    if status:
        stmt = stmt.where(ia.c.status == status)
    if asset_id:
        stmt = stmt.where(ia.c.asset_id == asset_id)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


class AlertReviewIn(StrictModel):
    status: str


@router.patch("/alerts/{alert_id}")
def review_alert(alert_id: str, body: AlertReviewIn,
                 user: Principal = Depends(require("assets", "edit"))):
    if body.status not in ("reviewed", "dismissed"):
        raise HTTPException(400, "status must be 'reviewed' or 'dismissed'")
    ia = t("ingested_alerts")
    try:
        with engine.begin() as conn:
            row = conn.execute(select(ia.c.id).where(
                ia.c.id == alert_id, ia.c.tenant_id == user.tenant_id)).first()
            if row is None:
                raise HTTPException(404, "alert not found")
            conn.execute(update(ia).where(ia.c.id == alert_id).values(
                status=body.status,
                reviewed_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
                reviewed_at=now_iso()))
        activity_log(tenant_id=user.tenant_id, actor_user_id=user.user_id, action=body.status,
                    entity_type="ingested_alert", entity_id=alert_id)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"review_alert failed for {alert_id}: {e}")
        raise HTTPException(500, "something went wrong — try again")


# ── compliance-config checks (P8-S3a) ───────────────────────────────────────────────────
# An agent reports a CONFIG FACT about an asset (MFA on, disk encryption on, patch level),
# not performance telemetry — the Vanta/Drata pattern, not xyOps' server monitoring. Reads
# for the SPA live on asset_detail (registers.py) and control_detail (library.py), following
# the same shape those pages already use for their other linked_* blocks — nothing new here.

class CheckItemIn(StrictModel):
    asset_id: str
    control_id: str | None = None
    check_key: str = Field(min_length=1, max_length=200)
    check_label: str = Field(min_length=1, max_length=200)
    status: str
    details: str | None = None
    expected_interval_minutes: int | None = None
    checked_at: IsoDate = None  # the agent's own observation time; defaults to server time


class ChecksIn(StrictModel):
    checks: list[CheckItemIn]


@router.post("/checks")
def ingest_checks(body: ChecksIn,
                  principal: IntegrationPrincipal = Depends(get_integration_principal)):
    """Upserts each item on (tenant, asset, check_key) — a latest-state table, not a log."""
    if not body.checks:
        raise HTTPException(400, "no checks supplied")
    if len(body.checks) > 500:
        raise HTTPException(400, "too many checks in one call (max 500)")
    cc, a, ctrl = t("compliance_checks"), t("assets"), t("controls")
    try:
        with engine.begin() as conn:
            now = now_iso()
            results = []
            for item in body.checks:
                if item.status not in CHECK_STATUSES:
                    raise HTTPException(400, f"status must be one of {', '.join(CHECK_STATUSES)}")
                if conn.execute(select(a.c.id).where(
                        a.c.id == item.asset_id,
                        a.c.tenant_id == principal.tenant_id)).first() is None:
                    raise HTTPException(404, f"asset not found: {item.asset_id}")
                if item.control_id is not None and conn.execute(select(ctrl.c.id).where(
                        ctrl.c.id == item.control_id,
                        ctrl.c.tenant_id == principal.tenant_id)).first() is None:
                    raise HTTPException(404, f"control not found: {item.control_id}")
                cur = conn.execute(select(cc.c.id).where(
                    cc.c.tenant_id == principal.tenant_id, cc.c.asset_id == item.asset_id,
                    cc.c.check_key == item.check_key)).first()
                # source is NEVER taken from the request body — it's always the resolved
                # token's own name, so one integration can't attribute facts to another's.
                vals = dict(
                    control_id=item.control_id, check_label=item.check_label,
                    status=item.status, details=item.details, source=principal.token_name,
                    expected_interval_minutes=item.expected_interval_minutes,
                    last_checked_at=item.checked_at or now, updated_at=now)
                if cur is None:
                    cid = str(uuid.uuid4())
                    conn.execute(insert(cc).values(
                        id=cid, tenant_id=principal.tenant_id, asset_id=item.asset_id,
                        check_key=item.check_key, created_at=now, **vals))
                else:
                    cid = cur[0]
                    conn.execute(update(cc).where(cc.c.id == cid).values(**vals))
                results.append({"id": cid, "asset_id": item.asset_id, "check_key": item.check_key})
        return {"checks": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ingest_checks failed: {e}")
        raise HTTPException(500, "something went wrong — try again")
