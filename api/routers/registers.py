"""Registers I — risk register, asset inventory, data inventory (Sprint 4a / M10a).

The live program a bank inspects: "show me your risk register and asset inventory."
  • Owners are PEOPLE (Sprint 1), never logins.
  • Risk scores auto-compute in the DB (inherent/residual = likelihood × impact); we add the band.
  • Risks link to controls / documents / assets via risk_links (arity + kind CHECK'd in the schema);
    a linked risk surfaces on the control's page (reverse nav) — see api/routers/library.py.

`risk_links` also permits OBLIGATION / THIRD_PARTY / INCIDENT targets; those tables have no data
until Sprint 4b, so the API actively supports CONTROL / DOCUMENT / ASSET here and the rest come
online for free when their registers land.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from api import activity
from api.auth import Principal, get_current_user
from api.permissions import require
from api.database import engine, get_conn, t
from api.util import IsoDate, StrictModel, now_iso, risk_band

router = APIRouter(tags=["registers"])

TREATMENTS = {"MITIGATED", "ACCEPTED", "AVOIDED", "TRANSFERRED"}
CRITICALITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
CLASSIFICATIONS = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}
# ── Sprint 4b (registers II) ──
TP_STATUS = {"ACTIVE", "OFFBOARDING", "TERMINATED"}
AGREEMENT_KINDS = {"DPA", "BAA", "NDA", "SLA", "MSA", "OTHER"}
SENSITIVITY = {"NONE", "LOW", "MEDIUM", "HIGH"}
IMPACT = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ASSESS_OUTCOME = {"PASS", "PASS_WITH_ACTIONS", "FAIL"}
OBLIGATION_TYPE = {"LEGAL", "CONTRACTUAL"}
OBLIGATION_STATUS = {"COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT"}
INCIDENT_STATUS = {"OPEN", "INVESTIGATING", "RESOLVED", "CLOSED"}

# kind → (table, column on risk_links). Only the first three are wired in the UI this sprint.
LINK_TARGETS = {
    "CONTROL": ("controls", "control_id"),
    "DOCUMENT": ("documents", "document_id"),
    "ASSET": ("assets", "asset_id"),
    "OBLIGATION": ("obligations", "obligation_id"),
    "THIRD_PARTY": ("third_parties", "third_party_id"),
    "INCIDENT": ("incidents", "incident_id"),
}


# ------------------------------------------------------------------ helpers

def _owner_must_be_person(conn, tenant_id, person_id):
    if person_id is None:
        return
    if conn.execute(select(t("people").c.id).where(
            t("people").c.id == person_id,
            t("people").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, "owner must be a person in this organisation")


def _get(conn, table, tenant_id, rid, what):
    r = conn.execute(select(t(table)).where(
        t(table).c.id == rid, t(table).c.tenant_id == tenant_id)).mappings().first()
    if r is None:
        raise HTTPException(404, f"{what} not found")
    return dict(r)


def _norm(vals: dict) -> dict:
    """Empty/whitespace strings → None, so an optional enum or reference sent as "" by a form
    clears the field instead of failing a CHECK / colliding on UNIQUE as an ugly 500."""
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in vals.items()}


def _reject_null_required(vals: dict, required: tuple[str, ...]):
    """A PATCH (or a blanked-out create) must not set a NOT NULL column to null — reject it
    with a clean 400 rather than letting the DB raise an uncaught IntegrityError (500)."""
    for k in required:
        if k in vals and vals[k] is None:
            raise HTTPException(400, f"{k.replace('_', ' ')} cannot be empty")


def _is_unique_violation(e: IntegrityError) -> bool:
    """SQLSTATE 23505 — so a reference-conflict maps to 409, but an unrelated constraint
    (e.g. a CHECK) surfaces as itself instead of being mislabelled 'reference already in use'."""
    return getattr(getattr(e, "orig", None), "sqlstate", None) == "23505"


def _owner_name(conn, person_id):
    if not person_id:
        return None
    return conn.execute(select(t("people").c.full_name).where(
        t("people").c.id == person_id)).scalar()


def _link_label(conn, kind, target_id):
    """A human label for a linked target (the three kinds we create this sprint)."""
    if kind == "CONTROL":
        r = conn.execute(select(t("controls").c.code, t("controls").c.statement).where(
            t("controls").c.id == target_id)).mappings().first()
        return f"{r['code']} — {r['statement']}" if r else None
    if kind == "DOCUMENT":
        return conn.execute(select(t("documents").c.title).where(
            t("documents").c.id == target_id)).scalar()
    if kind == "ASSET":
        return conn.execute(select(t("assets").c.name).where(
            t("assets").c.id == target_id)).scalar()
    return None


def _resolve_links(conn, risk_id):
    rl = t("risk_links")
    out = []
    for l in conn.execute(select(rl).where(rl.c.risk_id == risk_id)
                          .order_by(rl.c.created_at)).mappings():
        _table, col = LINK_TARGETS[l["target_kind"]]
        tid = l[col]
        out.append({"id": l["id"], "target_kind": l["target_kind"], "target_id": tid,
                    "label": _link_label(conn, l["target_kind"], tid), "note": l["note"]})
    return out


# ------------------------------------------------------------------ risks

class RiskIn(StrictModel):
    title: str
    reference: str | None = None
    description: str | None = None
    category: str | None = None
    owner_person_id: str | None = None
    inherent_likelihood: int | None = None
    inherent_impact: int | None = None
    residual_likelihood: int | None = None
    residual_impact: int | None = None
    treatment: str | None = None
    note: str | None = None
    status: str = "OPEN"
    next_review_at: IsoDate = None


def _validate_risk(vals: dict):
    _reject_null_required(vals, ("title", "status"))
    for f in ("inherent_likelihood", "inherent_impact",
              "residual_likelihood", "residual_impact"):
        v = vals.get(f)
        if v is not None and not (1 <= v <= 5):
            raise HTTPException(400, f"{f} must be between 1 and 5")
    if vals.get("treatment") and vals["treatment"] not in TREATMENTS:
        raise HTTPException(400, f"treatment must be one of {', '.join(sorted(TREATMENTS))}")
    if vals.get("status") and vals["status"] not in ("OPEN", "CLOSED"):
        raise HTTPException(400, "status must be OPEN or CLOSED")


def _band(d: dict) -> dict:
    d["inherent_band"] = risk_band(d["inherent_score"])
    d["residual_band"] = risk_band(d["residual_score"])
    return d


@router.get("/risks")
def list_risks(status: str | None = Query(None), q: str | None = Query(None),
               user: Principal = Depends(require("risks", "view")), conn=Depends(get_conn)):
    rk, ppl, rl = t("risks"), t("people"), t("risk_links")
    link_ct = (select(func.count()).select_from(rl)
               .where(rl.c.risk_id == rk.c.id).scalar_subquery())
    stmt = (select(rk, ppl.c.full_name.label("owner_name"), link_ct.label("link_count"))
            .select_from(rk).outerjoin(ppl, rk.c.owner_person_id == ppl.c.id)
            .where(rk.c.tenant_id == user.tenant_id)
            .order_by(rk.c.inherent_score.desc().nullslast(), rk.c.title))
    if status:
        stmt = stmt.where(rk.c.status == status)
    if q:
        stmt = stmt.where(func.lower(rk.c.title).like(f"%{q.lower()}%"))
    return [_band(dict(r)) for r in conn.execute(stmt).mappings()]


@router.post("/risks", status_code=201)
def create_risk(body: RiskIn, user: Principal = Depends(require("risks", "add"))):
    vals = _norm(body.model_dump())
    _validate_risk(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        rid, now = str(uuid.uuid4()), now_iso()
        if vals.get("reference") and conn.execute(select(t("risks").c.id).where(
                t("risks").c.tenant_id == user.tenant_id,
                t("risks").c.reference == vals["reference"])).first():
            raise HTTPException(409, f"reference {vals['reference']!r} is already in use")
        try:                                # backstop the pre-check against a concurrent insert
            conn.execute(insert(t("risks")).values(
                id=rid, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that reference is already in use")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="risk.created", entity_type="risk", entity_id=rid,
                 detail={"title": body.title})
    return {"id": rid}


@router.get("/risks/{risk_id}")
def risk_detail(risk_id: str, user: Principal = Depends(require("risks", "view")), conn=Depends(get_conn)):
    r = _get(conn, "risks", user.tenant_id, risk_id, "risk")
    return {**_band(r), "owner_name": _owner_name(conn, r["owner_person_id"]),
            "links": _resolve_links(conn, risk_id)}


class RiskPatch(StrictModel):
    title: str | None = None
    reference: str | None = None
    description: str | None = None
    category: str | None = None
    owner_person_id: str | None = None
    inherent_likelihood: int | None = None
    inherent_impact: int | None = None
    residual_likelihood: int | None = None
    residual_impact: int | None = None
    treatment: str | None = None
    note: str | None = None
    status: str | None = None
    next_review_at: IsoDate = None


@router.patch("/risks/{risk_id}")
def update_risk(risk_id: str, body: RiskPatch, user: Principal = Depends(require("risks", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_risk(vals)
    with engine.begin() as conn:
        _get(conn, "risks", user.tenant_id, risk_id, "risk")
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        vals["updated_at"] = now_iso()
        try:
            conn.execute(update(t("risks")).where(t("risks").c.id == risk_id).values(**vals))
        except IntegrityError as e:         # reference collides with another risk in this tenant
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that reference is already in use")
    return {"ok": True}


@router.delete("/risks/{risk_id}")
def delete_risk(risk_id: str, user: Principal = Depends(require("risks", "delete"))):
    with engine.begin() as conn:
        _get(conn, "risks", user.tenant_id, risk_id, "risk")
        conn.execute(delete(t("risks")).where(t("risks").c.id == risk_id))   # cascades risk_links
    return {"ok": True}


class LinkIn(StrictModel):
    target_kind: str
    target_id: str
    note: str | None = None


@router.post("/risks/{risk_id}/links", status_code=201)
def add_risk_link(risk_id: str, body: LinkIn, user: Principal = Depends(require("risks", "edit"))):
    kind = body.target_kind.upper()
    if kind not in LINK_TARGETS:
        raise HTTPException(400, f"target_kind must be one of {', '.join(LINK_TARGETS)}")
    table, col = LINK_TARGETS[kind]
    rl = t("risk_links")
    with engine.begin() as conn:
        _get(conn, "risks", user.tenant_id, risk_id, "risk")
        if conn.execute(select(t(table).c.id).where(
                t(table).c.id == body.target_id,
                t(table).c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, f"{kind.lower().replace('_', ' ')} not found in this organisation")
        if conn.execute(select(rl.c.id).where(
                rl.c.risk_id == risk_id, getattr(rl.c, col) == body.target_id)).first():
            raise HTTPException(409, "that link already exists")
        lid = str(uuid.uuid4())
        try:                                # uq_risk_link_target backstops a concurrent double-click
            conn.execute(insert(rl).values(
                id=lid, tenant_id=user.tenant_id, risk_id=risk_id, target_kind=kind,
                note=body.note, created_at=now_iso(), **{col: body.target_id}))
        except IntegrityError:
            raise HTTPException(409, "that link already exists")
    return {"id": lid, "label": _label_after(user.tenant_id, kind, body.target_id)}


def _label_after(tenant_id, kind, target_id):
    with engine.connect() as conn:
        return _link_label(conn, kind, target_id)


@router.delete("/risks/{risk_id}/links/{link_id}")
def delete_risk_link(risk_id: str, link_id: str, user: Principal = Depends(require("risks", "delete"))):
    rl = t("risk_links")
    with engine.begin() as conn:
        _get(conn, "risks", user.tenant_id, risk_id, "risk")
        conn.execute(delete(rl).where(
            rl.c.id == link_id, rl.c.risk_id == risk_id, rl.c.tenant_id == user.tenant_id))
    return {"ok": True}


# ------------------------------------------------------------------ assets

class AssetIn(StrictModel):
    name: str
    description: str | None = None
    asset_type: str = "VIRTUAL"
    owner_person_id: str | None = None
    quantity: int = 1
    data_types_stored: list[str] = []
    criticality: str | None = None
    location: str | None = None


def _validate_asset(vals: dict):
    _reject_null_required(vals, ("name", "asset_type", "quantity", "data_types_stored"))
    if vals.get("asset_type") and vals["asset_type"] not in ("PHYSICAL", "VIRTUAL"):
        raise HTTPException(400, "asset_type must be PHYSICAL or VIRTUAL")
    if vals.get("criticality") and vals["criticality"] not in CRITICALITIES:
        raise HTTPException(400, f"criticality must be one of {', '.join(sorted(CRITICALITIES))}")
    if vals.get("quantity") is not None and not (0 <= vals["quantity"] <= 2_147_483_647):
        raise HTTPException(400, "quantity must be between 0 and 2,147,483,647")
    if vals.get("data_types_stored") is not None and len(vals["data_types_stored"]) > 200:
        raise HTTPException(400, "too many data types (max 200)")


@router.get("/assets")
def list_assets(criticality: str | None = Query(None), q: str | None = Query(None),
                user: Principal = Depends(require("assets", "view")), conn=Depends(get_conn)):
    a, ppl = t("assets"), t("people")
    stmt = (select(a, ppl.c.full_name.label("owner_name"))
            .select_from(a).outerjoin(ppl, a.c.owner_person_id == ppl.c.id)
            .where(a.c.tenant_id == user.tenant_id).order_by(a.c.name))
    if criticality:
        stmt = stmt.where(a.c.criticality == criticality)
    if q:
        stmt = stmt.where(func.lower(a.c.name).like(f"%{q.lower()}%"))
    return [dict(r) for r in conn.execute(stmt).mappings()]


@router.post("/assets", status_code=201)
def create_asset(body: AssetIn, user: Principal = Depends(require("assets", "add"))):
    vals = _norm(body.model_dump())
    _validate_asset(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        aid, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("assets")).values(
            id=aid, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="asset.created", entity_type="asset", entity_id=aid,
                 detail={"name": body.name})
    return {"id": aid}


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: str, user: Principal = Depends(require("assets", "view")), conn=Depends(get_conn)):
    a = _get(conn, "assets", user.tenant_id, asset_id, "asset")
    stored = a.get("data_types_stored") or []
    # resolve stored data names against the data inventory so classification shows through
    matched = {}
    if stored:
        di = t("data_items")
        matched = {r["name"]: r["classification"] for r in conn.execute(
            select(di.c.name, di.c.classification).where(
                di.c.tenant_id == user.tenant_id, di.c.name.in_(stored))).mappings()}
    return {**a, "owner_name": _owner_name(conn, a["owner_person_id"]),
            "data": [{"name": n, "classification": matched.get(n)} for n in stored]}


class AssetPatch(StrictModel):
    name: str | None = None
    description: str | None = None
    asset_type: str | None = None
    owner_person_id: str | None = None
    quantity: int | None = None
    data_types_stored: list[str] | None = None
    criticality: str | None = None
    location: str | None = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, body: AssetPatch, user: Principal = Depends(require("assets", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_asset(vals)
    with engine.begin() as conn:
        _get(conn, "assets", user.tenant_id, asset_id, "asset")
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        vals["updated_at"] = now_iso()
        conn.execute(update(t("assets")).where(t("assets").c.id == asset_id).values(**vals))
    return {"ok": True}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, user: Principal = Depends(require("assets", "delete"))):
    with engine.begin() as conn:
        _get(conn, "assets", user.tenant_id, asset_id, "asset")
        conn.execute(delete(t("assets")).where(t("assets").c.id == asset_id))
    return {"ok": True}


# ------------------------------------------------------------------ data inventory

class DataItemIn(StrictModel):
    name: str
    description: str | None = None
    owner_person_id: str | None = None
    classification: str = "INTERNAL"
    retention_note: str | None = None


def _validate_data(vals: dict):
    _reject_null_required(vals, ("name", "classification"))
    if vals.get("classification") and vals["classification"] not in CLASSIFICATIONS:
        raise HTTPException(400, f"classification must be one of {', '.join(sorted(CLASSIFICATIONS))}")


@router.get("/data-items")
def list_data_items(q: str | None = Query(None),
                    user: Principal = Depends(require("data", "view")), conn=Depends(get_conn)):
    di, ppl = t("data_items"), t("people")
    stmt = (select(di, ppl.c.full_name.label("owner_name"))
            .select_from(di).outerjoin(ppl, di.c.owner_person_id == ppl.c.id)
            .where(di.c.tenant_id == user.tenant_id).order_by(di.c.name))
    if q:
        stmt = stmt.where(func.lower(di.c.name).like(f"%{q.lower()}%"))
    return [dict(r) for r in conn.execute(stmt).mappings()]


@router.post("/data-items", status_code=201)
def create_data_item(body: DataItemIn, user: Principal = Depends(require("data", "add"))):
    vals = _norm(body.model_dump())
    _validate_data(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        did, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("data_items")).values(
            id=did, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
    return {"id": did}


class DataItemPatch(StrictModel):
    name: str | None = None
    description: str | None = None
    owner_person_id: str | None = None
    classification: str | None = None
    retention_note: str | None = None


@router.patch("/data-items/{item_id}")
def update_data_item(item_id: str, body: DataItemPatch, user: Principal = Depends(require("data", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_data(vals)
    with engine.begin() as conn:
        _get(conn, "data_items", user.tenant_id, item_id, "data item")
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        vals["updated_at"] = now_iso()
        conn.execute(update(t("data_items")).where(t("data_items").c.id == item_id).values(**vals))
    return {"ok": True}


@router.delete("/data-items/{item_id}")
def delete_data_item(item_id: str, user: Principal = Depends(require("data", "delete"))):
    with engine.begin() as conn:
        _get(conn, "data_items", user.tenant_id, item_id, "data item")
        conn.execute(delete(t("data_items")).where(t("data_items").c.id == item_id))
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════ Sprint 4b / M10b
# Third parties (+ the 4th-party sub-processor tree banks ask us to disclose), their
# agreements (DPA/BAA/…) and security assessments (expiring — they feed the D-MOAT queue),
# the RBI obligations register (M2M to controls), and the incident register (RCA-gated close).

# ---------------------------------------------------------------- third parties

class ThirdPartyIn(StrictModel):
    name: str
    legal_name: str | None = None
    parent_third_party_id: str | None = None
    category: str | None = None
    countries: list[str] = []
    certifications: list[str] = []
    business_owner_person_id: str | None = None
    security_owner_person_id: str | None = None
    criticality: str | None = None
    status: str = "ACTIVE"


class ThirdPartyPatch(StrictModel):
    name: str | None = None
    legal_name: str | None = None
    parent_third_party_id: str | None = None
    category: str | None = None
    countries: list[str] | None = None
    certifications: list[str] | None = None
    business_owner_person_id: str | None = None
    security_owner_person_id: str | None = None
    criticality: str | None = None
    status: str | None = None


def _lock_vendor_tree(conn, tenant_id):
    """Serialize concurrent re-parent operations within a tenant. Without this, two PATCHes
    each pointing one vendor at the other update DISJOINT rows, so under READ COMMITTED neither
    cycle-check sees the other's pending write and a 2-node loop lands. A transaction advisory
    lock makes the loser wait, then its _would_cycle sees the committed parent and rejects."""
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"tp_tree:{tenant_id}"})


def _validate_tp(vals: dict):
    _reject_null_required(vals, ("name", "status", "countries", "certifications"))
    if vals.get("criticality") and vals["criticality"] not in CRITICALITIES:
        raise HTTPException(400, f"criticality must be one of {', '.join(sorted(CRITICALITIES))}")
    if vals.get("status") and vals["status"] not in TP_STATUS:
        raise HTTPException(400, f"status must be one of {', '.join(sorted(TP_STATUS))}")


def _tp_exists(conn, tenant_id, tp_id):
    return conn.execute(select(t("third_parties").c.id).where(
        t("third_parties").c.id == tp_id,
        t("third_parties").c.tenant_id == tenant_id)).first() is not None


def _would_cycle(conn, tenant_id, node_id, new_parent_id):
    """Setting node_id's parent to new_parent_id would make node its own ancestor. Walk UP
    from the proposed parent; a `seen` set keeps this terminating even on pre-existing bad data."""
    if new_parent_id is None:
        return False
    tp = t("third_parties")
    cur, seen = new_parent_id, set()
    while cur is not None and cur not in seen:
        if cur == node_id:
            return True
        seen.add(cur)
        cur = conn.execute(select(tp.c.parent_third_party_id).where(
            tp.c.id == cur, tp.c.tenant_id == tenant_id)).scalar()
    return False


def _validate_parent(conn, tenant_id, node_id, parent_id):
    if parent_id is None:
        return
    if parent_id == node_id:
        raise HTTPException(400, "a vendor cannot be its own parent")
    if not _tp_exists(conn, tenant_id, parent_id):
        raise HTTPException(400, "parent vendor not found in this organisation")
    if node_id and _would_cycle(conn, tenant_id, node_id, parent_id):
        raise HTTPException(400, "that would create a circular vendor chain")


@router.get("/third-parties")
def list_third_parties(q: str | None = Query(None), status: str | None = Query(None),
                       user: Principal = Depends(require("third_parties", "view")), conn=Depends(get_conn)):
    tp, ppl = t("third_parties"), t("people")
    parent = tp.alias("parent")
    stmt = (select(tp, ppl.c.full_name.label("business_owner_name"),
                   parent.c.name.label("parent_name"))
            .select_from(tp)
            .outerjoin(ppl, tp.c.business_owner_person_id == ppl.c.id)
            .outerjoin(parent, tp.c.parent_third_party_id == parent.c.id)
            .where(tp.c.tenant_id == user.tenant_id).order_by(tp.c.name))
    if status:
        stmt = stmt.where(tp.c.status == status)
    if q:
        stmt = stmt.where(func.lower(tp.c.name).like(f"%{q.lower()}%"))
    return [dict(r) for r in conn.execute(stmt).mappings()]


@router.post("/third-parties", status_code=201)
def create_third_party(body: ThirdPartyIn, user: Principal = Depends(require("third_parties", "add"))):
    vals = _norm(body.model_dump())
    _validate_tp(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.business_owner_person_id)
        _owner_must_be_person(conn, user.tenant_id, body.security_owner_person_id)
        _validate_parent(conn, user.tenant_id, None, body.parent_third_party_id)
        tid, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("third_parties")).values(
            id=tid, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="third_party.created", entity_type="third_party", entity_id=tid,
                 detail={"name": body.name})
    return {"id": tid}


@router.get("/third-parties/{tp_id}")
def third_party_detail(tp_id: str, user: Principal = Depends(require("third_parties", "view")), conn=Depends(get_conn)):
    from api.util import evidence_status, today_iso
    tp = _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
    today = today_iso()
    ag = t("third_party_agreements")
    agreements = [dict(r) for r in conn.execute(
        select(ag).where(ag.c.third_party_id == tp_id).order_by(ag.c.valid_until.nullslast())).mappings()]
    for a in agreements:
        a["expiry_status"] = evidence_status(a["valid_until"], today)
    asm = t("third_party_assessments")
    assessments = [dict(r) for r in conn.execute(
        select(asm).where(asm.c.third_party_id == tp_id).order_by(asm.c.expires_at.nullslast())).mappings()]
    for a in assessments:
        a["expiry_status"] = evidence_status(a["expires_at"], today)
    tpt = t("third_parties")
    children = [dict(r) for r in conn.execute(
        select(tpt.c.id, tpt.c.name).where(tpt.c.parent_third_party_id == tp_id)).mappings()]
    parent_name = conn.execute(select(tpt.c.name).where(
        tpt.c.id == tp["parent_third_party_id"])).scalar() if tp["parent_third_party_id"] else None
    return {**tp,
            "business_owner_name": _owner_name(conn, tp["business_owner_person_id"]),
            "security_owner_name": _owner_name(conn, tp["security_owner_person_id"]),
            "parent_name": parent_name,
            "agreements": agreements, "assessments": assessments, "children": children}


@router.patch("/third-parties/{tp_id}")
def update_third_party(tp_id: str, body: ThirdPartyPatch, user: Principal = Depends(require("third_parties", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_tp(vals)
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        for k in ("business_owner_person_id", "security_owner_person_id"):
            if k in vals:
                _owner_must_be_person(conn, user.tenant_id, vals[k])
        if "parent_third_party_id" in vals:
            _lock_vendor_tree(conn, user.tenant_id)   # serialize reparents before the cycle check
            _validate_parent(conn, user.tenant_id, tp_id, vals["parent_third_party_id"])
        vals["updated_at"] = now_iso()
        conn.execute(update(t("third_parties")).where(t("third_parties").c.id == tp_id).values(**vals))
    return {"ok": True}


@router.delete("/third-parties/{tp_id}")
def delete_third_party(tp_id: str, user: Principal = Depends(require("third_parties", "delete"))):
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        conn.execute(delete(t("third_parties")).where(t("third_parties").c.id == tp_id))
    return {"ok": True}


@router.get("/third-parties/{tp_id}/tree")
def third_party_tree(tp_id: str, user: Principal = Depends(require("third_parties", "view")), conn=Depends(get_conn)):
    """The vendor and its sub-processor chain (our vendor's vendor = the bank's 4th party),
    nested. Cycle-safe via a `seen` set even if the data somehow holds a loop."""
    tp = t("third_parties")
    _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
    rows = {r["id"]: dict(r) for r in conn.execute(
        select(tp.c.id, tp.c.name, tp.c.parent_third_party_id, tp.c.criticality, tp.c.status)
        .where(tp.c.tenant_id == user.tenant_id)).mappings()}
    kids: dict = {}
    for r in rows.values():
        kids.setdefault(r["parent_third_party_id"], []).append(r["id"])

    def build(nid, seen):
        if nid in seen:
            return None
        seen = seen | {nid}
        n = rows[nid]
        return {"id": nid, "name": n["name"], "criticality": n["criticality"],
                "status": n["status"],
                "children": [c for c in (build(k, seen) for k in
                                         sorted(kids.get(nid, []), key=lambda i: rows[i]["name"])) if c]}
    return build(tp_id, set())


# ---------------------------------------------------------------- agreements

class AgreementIn(StrictModel):
    kind: str
    reference: str | None = None
    valid_from: IsoDate = None
    valid_until: IsoDate = None
    notes: str | None = None


@router.post("/third-parties/{tp_id}/agreements", status_code=201)
def add_agreement(tp_id: str, body: AgreementIn, user: Principal = Depends(require("third_parties", "edit"))):
    if body.kind not in AGREEMENT_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(sorted(AGREEMENT_KINDS))}")
    vals = _norm(body.model_dump())
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        aid = str(uuid.uuid4())
        conn.execute(insert(t("third_party_agreements")).values(
            id=aid, tenant_id=user.tenant_id, third_party_id=tp_id, created_at=now_iso(), **vals))
    return {"id": aid}


@router.delete("/third-parties/{tp_id}/agreements/{agreement_id}")
def delete_agreement(tp_id: str, agreement_id: str, user: Principal = Depends(require("third_parties", "delete"))):
    ag = t("third_party_agreements")
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        conn.execute(delete(ag).where(
            ag.c.id == agreement_id, ag.c.third_party_id == tp_id, ag.c.tenant_id == user.tenant_id))
    return {"ok": True}


# ---------------------------------------------------------------- assessments

class AssessmentIn(StrictModel):
    assessed_at: IsoDate = None
    expires_at: IsoDate = None
    data_sensitivity: str | None = None
    business_impact: str | None = None
    outcome: str | None = None
    notes: str | None = None


def _validate_assessment(vals: dict):
    if vals.get("data_sensitivity") and vals["data_sensitivity"] not in SENSITIVITY:
        raise HTTPException(400, f"data_sensitivity must be one of {', '.join(sorted(SENSITIVITY))}")
    if vals.get("business_impact") and vals["business_impact"] not in IMPACT:
        raise HTTPException(400, f"business_impact must be one of {', '.join(sorted(IMPACT))}")
    if vals.get("outcome") and vals["outcome"] not in ASSESS_OUTCOME:
        raise HTTPException(400, f"outcome must be one of {', '.join(sorted(ASSESS_OUTCOME))}")


@router.post("/third-parties/{tp_id}/assessments", status_code=201)
def add_assessment(tp_id: str, body: AssessmentIn, user: Principal = Depends(require("third_parties", "edit"))):
    vals = _norm(body.model_dump())
    _validate_assessment(vals)
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        aid, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("third_party_assessments")).values(
            id=aid, tenant_id=user.tenant_id, third_party_id=tp_id,
            created_at=now, updated_at=now, **vals))
    return {"id": aid}


@router.patch("/third-parties/{tp_id}/assessments/{assessment_id}")
def update_assessment(tp_id: str, assessment_id: str, body: AssessmentIn,
                      user: Principal = Depends(require("third_parties", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_assessment(vals)
    asm = t("third_party_assessments")
    with engine.begin() as conn:
        row = conn.execute(select(asm).where(
            asm.c.id == assessment_id, asm.c.third_party_id == tp_id,
            asm.c.tenant_id == user.tenant_id)).first()
        if row is None:
            raise HTTPException(404, "assessment not found")
        vals["updated_at"] = now_iso()
        conn.execute(update(asm).where(asm.c.id == assessment_id).values(**vals))
    return {"ok": True}


@router.delete("/third-parties/{tp_id}/assessments/{assessment_id}")
def delete_assessment(tp_id: str, assessment_id: str, user: Principal = Depends(require("third_parties", "delete"))):
    asm = t("third_party_assessments")
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        conn.execute(delete(asm).where(
            asm.c.id == assessment_id, asm.c.third_party_id == tp_id, asm.c.tenant_id == user.tenant_id))
    return {"ok": True}


# ---------------------------------------------------------------- obligations (RBI)

class ObligationIn(StrictModel):
    requirement: str
    area: str | None = None
    source: str | None = None
    regulator: str | None = None
    type: str | None = None
    owner_person_id: str | None = None
    last_review_date: IsoDate = None
    next_review_date: IsoDate = None
    status: str = "PARTIALLY_COMPLIANT"


class ObligationPatch(StrictModel):
    requirement: str | None = None
    area: str | None = None
    source: str | None = None
    regulator: str | None = None
    type: str | None = None
    owner_person_id: str | None = None
    last_review_date: IsoDate = None
    next_review_date: IsoDate = None
    status: str | None = None


def _validate_obligation(vals: dict):
    _reject_null_required(vals, ("requirement", "status"))
    if vals.get("type") and vals["type"] not in OBLIGATION_TYPE:
        raise HTTPException(400, f"type must be one of {', '.join(sorted(OBLIGATION_TYPE))}")
    if vals.get("status") and vals["status"] not in OBLIGATION_STATUS:
        raise HTTPException(400, f"status must be one of {', '.join(sorted(OBLIGATION_STATUS))}")


def _obligation_controls(conn, obligation_id):
    com, ctrl = t("control_obligation_map"), t("controls")
    return [dict(r) for r in conn.execute(
        select(ctrl.c.id, ctrl.c.code, ctrl.c.statement)
        .join(com, com.c.control_id == ctrl.c.id)
        .where(com.c.obligation_id == obligation_id).order_by(ctrl.c.code)).mappings()]


@router.get("/obligations")
def list_obligations(regulator: str | None = Query(None), status: str | None = Query(None),
                     user: Principal = Depends(require("obligations", "view")), conn=Depends(get_conn)):
    ob, ppl, com = t("obligations"), t("people"), t("control_obligation_map")
    ctrl_ct = (select(func.count()).select_from(com)
               .where(com.c.obligation_id == ob.c.id).scalar_subquery())
    stmt = (select(ob, ppl.c.full_name.label("owner_name"), ctrl_ct.label("control_count"))
            .select_from(ob).outerjoin(ppl, ob.c.owner_person_id == ppl.c.id)
            .where(ob.c.tenant_id == user.tenant_id).order_by(ob.c.area.nullslast(), ob.c.requirement))
    if regulator:
        stmt = stmt.where(ob.c.regulator == regulator)
    if status:
        stmt = stmt.where(ob.c.status == status)
    return [dict(r) for r in conn.execute(stmt).mappings()]


@router.post("/obligations", status_code=201)
def create_obligation(body: ObligationIn, user: Principal = Depends(require("obligations", "add"))):
    vals = _norm(body.model_dump())
    _validate_obligation(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        oid, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("obligations")).values(
            id=oid, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="obligation.created", entity_type="obligation", entity_id=oid,
                 detail={"regulator": body.regulator})
    return {"id": oid}


@router.get("/obligations/{obligation_id}")
def obligation_detail(obligation_id: str, user: Principal = Depends(require("obligations", "view")), conn=Depends(get_conn)):
    ob = _get(conn, "obligations", user.tenant_id, obligation_id, "obligation")
    return {**ob, "owner_name": _owner_name(conn, ob["owner_person_id"]),
            "controls": _obligation_controls(conn, obligation_id)}


@router.patch("/obligations/{obligation_id}")
def update_obligation(obligation_id: str, body: ObligationPatch,
                      user: Principal = Depends(require("obligations", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    _validate_obligation(vals)
    with engine.begin() as conn:
        _get(conn, "obligations", user.tenant_id, obligation_id, "obligation")
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        vals["updated_at"] = now_iso()
        conn.execute(update(t("obligations")).where(
            t("obligations").c.id == obligation_id).values(**vals))
    return {"ok": True}


@router.delete("/obligations/{obligation_id}")
def delete_obligation(obligation_id: str, user: Principal = Depends(require("obligations", "delete"))):
    with engine.begin() as conn:
        _get(conn, "obligations", user.tenant_id, obligation_id, "obligation")
        conn.execute(delete(t("obligations")).where(t("obligations").c.id == obligation_id))
    return {"ok": True}


class ObligationControlIn(StrictModel):
    control_id: str
    note: str | None = None


@router.post("/obligations/{obligation_id}/controls", status_code=201)
def link_obligation_control(obligation_id: str, body: ObligationControlIn,
                            user: Principal = Depends(require("obligations", "edit"))):
    com, ctrl = t("control_obligation_map"), t("controls")
    with engine.begin() as conn:
        _get(conn, "obligations", user.tenant_id, obligation_id, "obligation")
        if conn.execute(select(ctrl.c.id).where(
                ctrl.c.id == body.control_id, ctrl.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "control not found in this organisation")
        try:
            conn.execute(insert(com).values(
                tenant_id=user.tenant_id, control_id=body.control_id,
                obligation_id=obligation_id, note=body.note, created_at=now_iso()))
        except IntegrityError:
            raise HTTPException(409, "that control is already linked")
    return {"ok": True}


@router.delete("/obligations/{obligation_id}/controls/{control_id}")
def unlink_obligation_control(obligation_id: str, control_id: str,
                              user: Principal = Depends(require("obligations", "delete"))):
    com = t("control_obligation_map")
    with engine.begin() as conn:
        _get(conn, "obligations", user.tenant_id, obligation_id, "obligation")
        conn.execute(delete(com).where(
            com.c.obligation_id == obligation_id, com.c.control_id == control_id,
            com.c.tenant_id == user.tenant_id))
    return {"ok": True}


# ---------------------------------------------------------------- incidents (RCA)

class IncidentIn(StrictModel):
    title: str
    reference: str | None = None
    description: str | None = None
    severity: str | None = None
    detected_at: IsoDate = None
    resolved_at: IsoDate = None
    root_cause: str | None = None
    lessons_learnt: str | None = None
    owner_person_id: str | None = None
    status: str = "OPEN"


class IncidentPatch(StrictModel):
    title: str | None = None
    reference: str | None = None
    description: str | None = None
    severity: str | None = None
    detected_at: IsoDate = None
    resolved_at: IsoDate = None
    root_cause: str | None = None
    lessons_learnt: str | None = None
    owner_person_id: str | None = None
    status: str | None = None


def _validate_incident(vals: dict, effective: dict):
    """`effective` is the row as it will be after the write (existing row merged with vals) —
    used to enforce inc_closed_needs_rca as a friendly 400 rather than a raw CHECK 500."""
    _reject_null_required(vals, ("title", "status"))
    if vals.get("severity") and vals["severity"] not in CRITICALITIES:
        raise HTTPException(400, f"severity must be one of {', '.join(sorted(CRITICALITIES))}")
    if vals.get("status") and vals["status"] not in INCIDENT_STATUS:
        raise HTTPException(400, f"status must be one of {', '.join(sorted(INCIDENT_STATUS))}")
    if effective.get("status") == "CLOSED" and not effective.get("root_cause"):
        raise HTTPException(400, "an incident can't be CLOSED without a root cause")


@router.get("/incidents")
def list_incidents(status: str | None = Query(None), q: str | None = Query(None),
                   user: Principal = Depends(require("incidents", "view")), conn=Depends(get_conn)):
    inc, ppl = t("incidents"), t("people")
    stmt = (select(inc, ppl.c.full_name.label("owner_name"))
            .select_from(inc).outerjoin(ppl, inc.c.owner_person_id == ppl.c.id)
            .where(inc.c.tenant_id == user.tenant_id)
            .order_by(inc.c.detected_at.desc().nullslast(), inc.c.title))
    if status:
        stmt = stmt.where(inc.c.status == status)
    if q:
        stmt = stmt.where(func.lower(inc.c.title).like(f"%{q.lower()}%"))
    return [dict(r) for r in conn.execute(stmt).mappings()]


@router.post("/incidents", status_code=201)
def create_incident(body: IncidentIn, user: Principal = Depends(require("incidents", "add"))):
    vals = _norm(body.model_dump())
    _validate_incident(vals, vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        iid, now = str(uuid.uuid4()), now_iso()
        try:
            conn.execute(insert(t("incidents")).values(
                id=iid, tenant_id=user.tenant_id, created_at=now, updated_at=now, **vals))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that reference is already in use")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="incident.created", entity_type="incident", entity_id=iid,
                 detail={"title": body.title})
    return {"id": iid}


@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: str, user: Principal = Depends(require("incidents", "view")), conn=Depends(get_conn)):
    inc = _get(conn, "incidents", user.tenant_id, incident_id, "incident")
    return {**inc, "owner_name": _owner_name(conn, inc["owner_person_id"])}


@router.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, body: IncidentPatch,
                    user: Principal = Depends(require("incidents", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    with engine.begin() as conn:
        cur = _get(conn, "incidents", user.tenant_id, incident_id, "incident")
        _validate_incident(vals, {**cur, **vals})    # enforce RCA-on-close against the merged row
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        vals["updated_at"] = now_iso()
        try:
            conn.execute(update(t("incidents")).where(
                t("incidents").c.id == incident_id).values(**vals))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that reference is already in use")
    return {"ok": True}


@router.delete("/incidents/{incident_id}")
def delete_incident(incident_id: str, user: Principal = Depends(require("incidents", "delete"))):
    with engine.begin() as conn:
        _get(conn, "incidents", user.tenant_id, incident_id, "incident")
        conn.execute(delete(t("incidents")).where(t("incidents").c.id == incident_id))
    return {"ok": True}
