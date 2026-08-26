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

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from api.core import activity, storage
from api.domain import importer, register_imports, vocabularies
from api.rendering import xlsx_io
from api.core.config import settings
from api.core.auth import Principal, get_current_user
from api.core.permissions import MODULE_LABELS, load_permissions, require
from api.core.database import engine, get_conn, t
from api.core.util import (IsoDate, StrictModel, effective_liveness, effective_status,
                          now_iso, risk_band)

router = APIRouter(tags=["registers"])

# Single source of truth is api/vocabularies.py — register_imports.py needs the same values
# and cannot import this module. Kept as sets here because the validators below use `in`.
TREATMENTS = set(vocabularies.TREATMENTS)
CRITICALITIES = set(vocabularies.CRITICALITIES)
CLASSIFICATIONS = set(vocabularies.CLASSIFICATIONS)
# ── Sprint 4b (registers II) ──
TP_STATUS = set(vocabularies.TP_STATUSES)
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

def _person_must_exist(conn, tenant_id, person_id, what="owner"):
    """Generalised from _owner_must_be_person — P4-S7 gave risks three person references
    (owner, reported_by, reviewed_by) and only the owner was ever validated."""
    if person_id is None:
        return
    if conn.execute(select(t("people").c.id).where(
            t("people").c.id == person_id,
            t("people").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, f"{what} must be a person in this organisation")


#: the original name, kept for its existing call sites
_owner_must_be_person = _person_must_exist


def _tp_must_exist(conn, tenant_id, tp_id):
    if tp_id is None:
        return
    if conn.execute(select(t("third_parties").c.id).where(
            t("third_parties").c.id == tp_id,
            t("third_parties").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, "vendor not found in this organisation")


def _evidence_must_exist(conn, tenant_id, evidence_id):
    # 400 here, not the 404 assessments.py uses for the same shape — this file's own
    # convention is 400 "<thing> not found in this organisation" everywhere else.
    if evidence_id is None:
        return
    if conn.execute(select(t("evidence").c.id).where(
            t("evidence").c.id == evidence_id,
            t("evidence").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, "evidence not found in this organisation")


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
    reported_by_person_id: str | None = None
    reviewed_by_person_id: str | None = None
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
        for key, label in (("owner_person_id", "owner"),
                           ("reported_by_person_id", "reporter"),
                           ("reviewed_by_person_id", "reviewer")):
            _person_must_exist(conn, user.tenant_id, vals.get(key), label)
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
    ev, re_ = t("evidence"), t("risk_evidence")
    evidence = [dict(x) for x in conn.execute(
        select(ev.c.id, ev.c.title, ev.c.evidence_type, ev.c.valid_until)
        .join(re_, re_.c.evidence_id == ev.c.id)
        .where(re_.c.risk_id == risk_id)
        .order_by(ev.c.valid_until.asc().nullslast())).mappings()]
    return {**_band(r), "owner_name": _owner_name(conn, r["owner_person_id"]),
            "reported_by_name": _owner_name(conn, r["reported_by_person_id"]),
            "reviewed_by_name": _owner_name(conn, r["reviewed_by_person_id"]),
            "links": _resolve_links(conn, risk_id), "evidence": evidence}


class RiskPatch(StrictModel):
    title: str | None = None
    reference: str | None = None
    description: str | None = None
    category: str | None = None
    owner_person_id: str | None = None
    reported_by_person_id: str | None = None
    reviewed_by_person_id: str | None = None
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
        # all three person refs, not just the owner — reported_by/reviewed_by were a
        # cross-tenant 500 waiting to happen the moment the UI could set them
        for key, label in (("owner_person_id", "owner"),
                           ("reported_by_person_id", "reporter"),
                           ("reviewed_by_person_id", "reviewer")):
            if key in vals:
                _person_must_exist(conn, user.tenant_id, vals[key], label)
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
        try:
            conn.execute(delete(t("risks")).where(t("risks").c.id == risk_id))  # cascades risk_links
        except IntegrityError:
            # `findings.risk_id` is ON DELETE RESTRICT (db/schema.sql:1439) and there is no
            # global exception handler in api/main.py, so this surfaced as a raw 500. It had
            # to be fixed BEFORE the DataTable bulk delete shipped: a bulk run over a
            # register containing one cited risk would otherwise produce a wall of 500s with
            # nothing legible to show per row. Mirrors delete_third_party's guard.
            raise HTTPException(
                409, "this risk is cited by an audit finding — close or reassign that "
                     "finding first")
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
    vendor_third_party_id: str | None = None
    subtype: str | None = None
    # PHYSICAL-only
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    # VIRTUAL-only
    hostname: str | None = None
    ip_address: str | None = None
    cloud_provider: str | None = None
    service_url: str | None = None
    expected_heartbeat_minutes: int | None = None


PHYSICAL_ONLY = ("manufacturer", "model", "serial_number")
VIRTUAL_ONLY = ("hostname", "ip_address", "cloud_provider", "service_url")


def _validate_asset(vals: dict, effective: dict | None = None):
    """`effective` is the row as it will be AFTER the write (current merged with vals).

    The PHYSICAL/VIRTUAL shape rule cannot be checked against the submitted dict alone: a
    PATCH sending only `hostname` carries no asset_type, so a naive check silently passes.
    Deliberately enforced here and not as a DB CHECK — a CHECK would make flipping
    asset_type on a populated row a 500 instead of a fixable 400.
    """
    _reject_null_required(vals, ("name", "asset_type", "quantity", "data_types_stored"))
    if vals.get("asset_type") and vals["asset_type"] not in ("PHYSICAL", "VIRTUAL"):
        raise HTTPException(400, "asset_type must be PHYSICAL or VIRTUAL")
    if vals.get("criticality") and vals["criticality"] not in CRITICALITIES:
        raise HTTPException(400, f"criticality must be one of {', '.join(sorted(CRITICALITIES))}")
    if vals.get("quantity") is not None and not (0 <= vals["quantity"] <= 2_147_483_647):
        raise HTTPException(400, "quantity must be between 0 and 2,147,483,647")
    if vals.get("data_types_stored") is not None and len(vals["data_types_stored"]) > 200:
        raise HTTPException(400, "too many data types (max 200)")
    if vals.get("expected_heartbeat_minutes") is not None and vals["expected_heartbeat_minutes"] <= 0:
        raise HTTPException(400, "expected heartbeat interval must be a positive number of minutes")
    eff = effective if effective is not None else vals
    if eff.get("asset_type") in ("PHYSICAL", "VIRTUAL"):
        wrong = PHYSICAL_ONLY if eff["asset_type"] == "VIRTUAL" else VIRTUAL_ONLY
        bad = [k for k in wrong if eff.get(k)]
        if bad:
            raise HTTPException(400, f"a {eff['asset_type'].lower()} asset cannot have "
                                     f"{', '.join(b.replace('_', ' ') for b in bad)}")


@router.get("/assets")
def list_assets(criticality: str | None = Query(None), q: str | None = Query(None),
                user: Principal = Depends(require("assets", "view")), conn=Depends(get_conn)):
    a, ppl, tp = t("assets"), t("people"), t("third_parties")
    stmt = (select(a, ppl.c.full_name.label("owner_name"), tp.c.name.label("vendor_name"))
            .select_from(a).outerjoin(ppl, a.c.owner_person_id == ppl.c.id)
            .outerjoin(tp, a.c.vendor_third_party_id == tp.c.id)
            .where(a.c.tenant_id == user.tenant_id).order_by(a.c.name))
    if criticality:
        stmt = stmt.where(a.c.criticality == criticality)
    if q:
        stmt = stmt.where(func.lower(a.c.name).like(f"%{q.lower()}%"))
    now = now_iso()
    return [{**dict(r), "effective_liveness": effective_liveness(
                r["last_seen_at"], r["expected_heartbeat_minutes"], now)}
            for r in conn.execute(stmt).mappings()]


@router.post("/assets", status_code=201)
def create_asset(body: AssetIn, user: Principal = Depends(require("assets", "add"))):
    vals = _norm(body.model_dump())
    _validate_asset(vals)
    with engine.begin() as conn:
        _owner_must_be_person(conn, user.tenant_id, body.owner_person_id)
        _tp_must_exist(conn, user.tenant_id, vals.get("vendor_third_party_id"))
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
    vendor_name = conn.execute(select(t("third_parties").c.name).where(
        t("third_parties").c.id == a["vendor_third_party_id"])).scalar() \
        if a["vendor_third_party_id"] else None
    photo_name = conn.execute(select(t("files").c.original_name).where(
        t("files").c.id == a["photo_file_id"])).scalar() if a["photo_file_id"] else None
    now = now_iso()
    cc = t("compliance_checks")
    checks = [
        {**dict(c), "effective_status": effective_status(
            c["status"], c["last_checked_at"], c["expected_interval_minutes"], now)}
        for c in conn.execute(
            select(cc).where(cc.c.tenant_id == user.tenant_id, cc.c.asset_id == asset_id)
            .order_by(cc.c.check_label)).mappings()
    ]
    return {**a, "owner_name": _owner_name(conn, a["owner_person_id"]),
            "vendor_name": vendor_name, "photo_name": photo_name,
            "effective_liveness": effective_liveness(
                a["last_seen_at"], a["expected_heartbeat_minutes"], now),
            "compliance_checks": checks,
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
    vendor_third_party_id: str | None = None
    subtype: str | None = None
    # PHYSICAL-only
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    # VIRTUAL-only
    hostname: str | None = None
    ip_address: str | None = None
    cloud_provider: str | None = None
    service_url: str | None = None
    expected_heartbeat_minutes: int | None = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, body: AssetPatch, user: Principal = Depends(require("assets", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    with engine.begin() as conn:
        cur = _get(conn, "assets", user.tenant_id, asset_id, "asset")
        # Switching type clears the other side's columns, so the row can never hold
        # contradictory detail — and so the shape check below sees the real end state.
        if vals.get("asset_type") and vals["asset_type"] != cur["asset_type"]:
            drop = VIRTUAL_ONLY if vals["asset_type"] == "PHYSICAL" else PHYSICAL_ONLY
            vals.update({k: None for k in drop})
        _validate_asset(vals, {**cur, **vals})
        if "owner_person_id" in vals:
            _owner_must_be_person(conn, user.tenant_id, vals["owner_person_id"])
        if "vendor_third_party_id" in vals:
            _tp_must_exist(conn, user.tenant_id, vals["vendor_third_party_id"])
        vals["updated_at"] = now_iso()
        conn.execute(update(t("assets")).where(t("assets").c.id == asset_id).values(**vals))
    return {"ok": True}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, user: Principal = Depends(require("assets", "delete"))):
    files_t = t("files")
    with engine.begin() as conn:
        a = _get(conn, "assets", user.tenant_id, asset_id, "asset")
        key = conn.execute(select(files_t.c.storage_key).where(
            files_t.c.id == a["photo_file_id"])).scalar() if a["photo_file_id"] else None
        conn.execute(delete(t("assets")).where(t("assets").c.id == asset_id))
        if a["photo_file_id"]:      # after the asset — files.id is RESTRICT-referenced
            conn.execute(delete(files_t).where(files_t.c.id == a["photo_file_id"]))
    if key:                         # only once the row is gone, same order as evidence.py
        storage.delete(key)
    return {"ok": True}


# ---------------------------------------------------------------- asset photo (P4-S7)
#: A rack photo is an attribute of the asset, so it lands in `files` rather than the
#: evidence vault — see the schema comment on assets.photo_file_id.
#: Raster only. The photo is rendered straight back into an <img>, and while a blob-URL
#: <img> will not execute script inside an SVG, allowing image/svg+xml here means the
#: same bytes are one `?disposition=inline` away from being a scripted document.
PHOTO_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/assets/{asset_id}/photo", status_code=201)
async def upload_asset_photo(asset_id: str, file: UploadFile = File(...),
                             user: Principal = Depends(require("assets", "edit"))):
    if (file.content_type or "").lower() not in PHOTO_MIME:
        raise HTTPException(400, "photo must be a PNG, JPEG, WebP or GIF image")
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB limit")

    a_t, files_t, members = t("assets"), t("files"), t("tenant_members")
    with engine.connect() as conn:      # 404 before anything is written to the vault
        a = conn.execute(select(a_t.c.id, a_t.c.photo_file_id).where(
            a_t.c.id == asset_id, a_t.c.tenant_id == user.tenant_id)).mappings().first()
    if a is None:
        raise HTTPException(404, "asset not found")

    key, sha, size = storage.save(user.tenant_id, data, file.filename or "photo")
    file_id, now = str(uuid.uuid4()), now_iso()
    old_key = None
    with engine.begin() as conn:
        member_id = conn.execute(select(members.c.id).where(
            members.c.tenant_id == user.tenant_id,
            members.c.user_id == user.user_id)).scalar()
        if a["photo_file_id"]:          # replacing: remember the old blob to sweep after commit
            old_key = conn.execute(select(files_t.c.storage_key).where(
                files_t.c.id == a["photo_file_id"])).scalar()
        conn.execute(insert(files_t).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "photo", mime_type=file.content_type,
            size_bytes=size, sha256=sha, uploaded_by_member_id=member_id, created_at=now))
        conn.execute(update(a_t).where(a_t.c.id == asset_id).values(
            photo_file_id=file_id, updated_at=now))
        if a["photo_file_id"]:
            conn.execute(delete(files_t).where(files_t.c.id == a["photo_file_id"]))
    if old_key:
        storage.delete(old_key)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="asset.photo_uploaded", entity_type="asset", entity_id=asset_id,
                 detail={"sha256": sha})
    return {"file_id": file_id, "original_name": file.filename, "size_bytes": size, "sha256": sha}


@router.get("/assets/{asset_id}/photo")
def download_asset_photo(asset_id: str,
                         disposition: str = Query("inline", pattern="^(attachment|inline)$"),
                         user: Principal = Depends(require("assets", "view")),
                         conn=Depends(get_conn)):
    a_t, files_t = t("assets"), t("files")
    row = conn.execute(
        select(files_t.c.storage_key, files_t.c.original_name, files_t.c.mime_type)
        .join(a_t, a_t.c.photo_file_id == files_t.c.id)
        .where(a_t.c.id == asset_id, a_t.c.tenant_id == user.tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "no photo on this asset")
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "file missing from vault")
    name = (row["original_name"] or "photo").replace('"', "")
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream",
                        headers={"Content-Disposition": f'{disposition}; filename="{name}"'})


@router.delete("/assets/{asset_id}/photo")
def delete_asset_photo(asset_id: str, user: Principal = Depends(require("assets", "edit"))):
    """Gated on `edit`, not `delete`: removing a photo edits the asset, it does not destroy
    the record. The same reasoning as clearing any other field."""
    a_t, files_t = t("assets"), t("files")
    with engine.begin() as conn:
        a = _get(conn, "assets", user.tenant_id, asset_id, "asset")
        if not a["photo_file_id"]:
            raise HTTPException(404, "no photo on this asset")
        key = conn.execute(select(files_t.c.storage_key).where(
            files_t.c.id == a["photo_file_id"])).scalar()
        conn.execute(update(a_t).where(a_t.c.id == asset_id).values(
            photo_file_id=None, updated_at=now_iso()))
        conn.execute(delete(files_t).where(files_t.c.id == a["photo_file_id"]))
    if key:
        storage.delete(key)
    return {"ok": True}


# ------------------------------------------------------------------ data inventory

class DataItemIn(StrictModel):
    name: str
    description: str | None = None
    data_type: str | None = None            # lookup_values kind='data_type'
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
    data_type: str | None = None
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
    from api.core.util import evidence_status, today_iso
    tp = _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
    today = today_iso()
    ag = t("third_party_agreements")
    agreements = [dict(r) for r in conn.execute(
        select(ag).where(ag.c.third_party_id == tp_id).order_by(ag.c.valid_until.nullslast())).mappings()]
    files_t = t("files")
    file_names = dict(conn.execute(
        select(files_t.c.id, files_t.c.original_name).where(
            files_t.c.tenant_id == user.tenant_id,
            files_t.c.id.in_([a["file_id"] for a in agreements if a["file_id"]] or [""]))).all())
    for a in agreements:
        a["expiry_status"] = evidence_status(a["valid_until"], today)
        a["file_name"] = file_names.get(a["file_id"])
    asm = t("third_party_assessments")
    assessments = [dict(r) for r in conn.execute(
        select(asm).where(asm.c.third_party_id == tp_id).order_by(asm.c.expires_at.nullslast())).mappings()]
    ev_titles = dict(conn.execute(
        select(t("evidence").c.id, t("evidence").c.title).where(
            t("evidence").c.tenant_id == user.tenant_id,
            t("evidence").c.id.in_([a["evidence_id"] for a in assessments if a["evidence_id"]] or [""]))).all())
    for a in assessments:
        a["expiry_status"] = evidence_status(a["expires_at"], today)
        a["evidence_title"] = ev_titles.get(a["evidence_id"])
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
        try:
            conn.execute(delete(t("third_parties")).where(t("third_parties").c.id == tp_id))
        except IntegrityError:
            # assets.vendor_third_party_id is ON DELETE RESTRICT. That edge was harmless
            # while the column was dormant; the moment P4-S7 let a user set it, deleting a
            # vendor an asset points at became an unhandled 500.
            raise HTTPException(
                409, "this vendor is still named on an asset — clear it there first")
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


class AgreementPatch(StrictModel):
    """Its own model rather than reusing AgreementIn: `kind` is required there, so a PATCH
    that only fixes an expiry date would have to resend it.

    `file_id` is deliberately NOT on the wire in either model. The FK is composite
    (file_id, tenant_id), so an unvalidated id from a caller would be a cross-tenant
    IntegrityError → 500; the upload and delete endpoints below own that column.
    """
    kind: str | None = None
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


@router.patch("/third-parties/{tp_id}/agreements/{agreement_id}")
def update_agreement(tp_id: str, agreement_id: str, body: AgreementPatch,
                     user: Principal = Depends(require("third_parties", "edit"))):
    """There was no PATCH at all before P4-S7 — only POST and DELETE — so a contract could
    only ever be attached in the same instant the agreement was created."""
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    if vals.get("kind") and vals["kind"] not in AGREEMENT_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(sorted(AGREEMENT_KINDS))}")
    ag = t("third_party_agreements")
    with engine.begin() as conn:
        row = conn.execute(select(ag).where(
            ag.c.id == agreement_id, ag.c.third_party_id == tp_id,
            ag.c.tenant_id == user.tenant_id)).first()
        if row is None:
            raise HTTPException(404, "agreement not found")
        conn.execute(update(ag).where(ag.c.id == agreement_id).values(**vals))
    return {"ok": True}


@router.post("/third-parties/{tp_id}/agreements/{agreement_id}/file", status_code=201)
async def upload_agreement_file(tp_id: str, agreement_id: str, file: UploadFile = File(...),
                                user: Principal = Depends(require("third_parties", "edit"))):
    """Attach the signed contract to an agreement.

    Goes into `files`, NOT the evidence vault — third_party_agreements.file_id FKs `files`
    directly while third_party_assessments.evidence_id FKs `evidence`, and that asymmetry
    is deliberate: routing an MSA through Evidence would force a title and evidence_type on
    it, list it in the artifact vault, and double-count its expiry against the agreement's
    own valid_until.
    """
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB limit")

    ag, files_t, members = t("third_party_agreements"), t("files"), t("tenant_members")
    with engine.connect() as conn:      # 404 before writing anything to the vault
        row = conn.execute(select(ag.c.id, ag.c.file_id).where(
            ag.c.id == agreement_id, ag.c.third_party_id == tp_id,
            ag.c.tenant_id == user.tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "agreement not found")

    key, sha, size = storage.save(user.tenant_id, data, file.filename or "contract")
    file_id, now = str(uuid.uuid4()), now_iso()
    old_key = None
    with engine.begin() as conn:
        member_id = conn.execute(select(members.c.id).where(
            members.c.tenant_id == user.tenant_id,
            members.c.user_id == user.user_id)).scalar()
        if row["file_id"]:              # replacing: remember the old blob to sweep after commit
            old_key = conn.execute(select(files_t.c.storage_key).where(
                files_t.c.id == row["file_id"])).scalar()
        conn.execute(insert(files_t).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "contract", mime_type=file.content_type,
            size_bytes=size, sha256=sha, uploaded_by_member_id=member_id, created_at=now))
        conn.execute(update(ag).where(ag.c.id == agreement_id).values(file_id=file_id))
        if row["file_id"]:
            conn.execute(delete(files_t).where(files_t.c.id == row["file_id"]))
    if old_key:                          # only after the row is gone — same order as evidence.py
        storage.delete(old_key)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="third_party.agreement_file_uploaded", entity_type="third_party",
                 entity_id=tp_id, detail={"agreement_id": agreement_id, "sha256": sha})
    return {"file_id": file_id, "original_name": file.filename, "size_bytes": size,
            "sha256": sha}


@router.get("/third-parties/{tp_id}/agreements/{agreement_id}/file")
def download_agreement_file(tp_id: str, agreement_id: str,
                            disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
                            user: Principal = Depends(require("third_parties", "view")),
                            conn=Depends(get_conn)):
    ag, files_t = t("third_party_agreements"), t("files")
    row = conn.execute(
        select(files_t.c.storage_key, files_t.c.original_name, files_t.c.mime_type)
        .join(ag, ag.c.file_id == files_t.c.id)
        .where(ag.c.id == agreement_id, ag.c.third_party_id == tp_id,
               ag.c.tenant_id == user.tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "file not found")
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "file missing from vault")
    name = (row["original_name"] or "contract").replace('"', "")
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream",
                        headers={"Content-Disposition": f'{disposition}; filename="{name}"'})


@router.delete("/third-parties/{tp_id}/agreements/{agreement_id}")
def delete_agreement(tp_id: str, agreement_id: str, user: Principal = Depends(require("third_parties", "delete"))):
    ag, files_t = t("third_party_agreements"), t("files")
    with engine.begin() as conn:
        _get(conn, "third_parties", user.tenant_id, tp_id, "third party")
        row = conn.execute(select(ag.c.file_id).where(
            ag.c.id == agreement_id, ag.c.third_party_id == tp_id,
            ag.c.tenant_id == user.tenant_id)).mappings().first()
        key = None
        if row and row["file_id"]:
            key = conn.execute(select(files_t.c.storage_key).where(
                files_t.c.id == row["file_id"])).scalar()
        conn.execute(delete(ag).where(
            ag.c.id == agreement_id, ag.c.third_party_id == tp_id, ag.c.tenant_id == user.tenant_id))
        if row and row["file_id"]:      # files.id is RESTRICT-referenced, so delete after the agreement
            conn.execute(delete(files_t).where(files_t.c.id == row["file_id"]))
    if key:
        storage.delete(key)
    return {"ok": True}


# ---------------------------------------------------------------- assessments

class AssessmentIn(StrictModel):
    assessed_at: IsoDate = None
    expires_at: IsoDate = None
    data_sensitivity: str | None = None
    business_impact: str | None = None
    outcome: str | None = None
    notes: str | None = None
    evidence_id: str | None = None          # the questionnaire/report backing this review


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
        _evidence_must_exist(conn, user.tenant_id, vals.get("evidence_id"))
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
        if "evidence_id" in vals:
            _evidence_must_exist(conn, user.tenant_id, vals["evidence_id"])
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
    # P5-S4: values come from the `incident_category` lookup kind, seeded in P4-S3 and
    # unused until now because the column did not exist. Free text like every other
    # lookup-backed field — the vocabulary is a UI affordance, not a DB constraint, so an
    # admin can extend it from Masters without a migration.
    category: str | None = None
    detected_at: IsoDate = None
    resolved_at: IsoDate = None
    root_cause: str | None = None
    resolution: str | None = None
    corrective_action: str | None = None
    lessons_learnt: str | None = None
    owner_person_id: str | None = None
    status: str = "OPEN"


class IncidentPatch(StrictModel):
    title: str | None = None
    reference: str | None = None
    description: str | None = None
    severity: str | None = None
    category: str | None = None
    detected_at: IsoDate = None
    resolved_at: IsoDate = None
    root_cause: str | None = None
    resolution: str | None = None
    corrective_action: str | None = None
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
    ie, users_t = t("incident_events"), t("users")
    events = [dict(x) for x in conn.execute(
        select(ie.c.id, ie.c.event_type, ie.c.body, ie.c.author_kind,
               ie.c.occurred_at, ie.c.created_at,
               users_t.c.full_name.label("author_name"))
        .select_from(ie).outerjoin(users_t, ie.c.author_user_id == users_t.c.id)
        # occurred_at then seq — NOT created_at. Both are second-resolution, so two entries
        # logged in the same second tie on every timestamp the table has; seq is the only
        # thing that preserves the order they were actually written in.
        .where(ie.c.incident_id == incident_id)
        .order_by(ie.c.occurred_at, ie.c.seq)).mappings()]
    ev, iev = t("evidence"), t("incident_evidence")
    evidence = [dict(x) for x in conn.execute(
        select(ev.c.id, ev.c.title, ev.c.evidence_type, ev.c.valid_until)
        .join(iev, iev.c.evidence_id == ev.c.id)
        .where(iev.c.incident_id == incident_id)
        .order_by(ev.c.valid_until.asc().nullslast())).mappings()]
    return {**inc, "owner_name": _owner_name(conn, inc["owner_person_id"]),
            "events": events, "evidence": evidence}


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


# ---------------------------------------------------------------- register ↔ evidence (P4-S7)
# A separate join table per register rather than a 7th risk_links kind — see the schema
# comment on risk_evidence for why (five hand-maintained artifacts vs an 8-line table).

class EvidenceLinkIn(StrictModel):
    evidence_id: str


def _attach_evidence(join_table: str, owner_table: str, owner_col: str, what: str,
                     tenant_id: str, owner_id: str, evidence_id: str):
    with engine.begin() as conn:
        _get(conn, owner_table, tenant_id, owner_id, what)
        _evidence_must_exist(conn, tenant_id, evidence_id)
        try:
            # tenant_id omitted on purpose — the inherit_tenant trigger fills it from the
            # parent, matching how review_messages and evidence_controls are written.
            conn.execute(insert(t(join_table)).values(
                **{owner_col: owner_id, "evidence_id": evidence_id}))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that evidence is already attached")
    return {"ok": True}


def _detach_evidence(join_table: str, owner_table: str, owner_col: str, what: str,
                     tenant_id: str, owner_id: str, evidence_id: str):
    j = t(join_table)
    with engine.begin() as conn:
        _get(conn, owner_table, tenant_id, owner_id, what)
        conn.execute(delete(j).where(
            j.c.tenant_id == tenant_id, j.c[owner_col] == owner_id,
            j.c.evidence_id == evidence_id))
    return {"ok": True}


@router.post("/risks/{risk_id}/evidence", status_code=201)
def attach_risk_evidence(risk_id: str, body: EvidenceLinkIn,
                         user: Principal = Depends(require("risks", "edit"))):
    return _attach_evidence("risk_evidence", "risks", "risk_id", "risk",
                            user.tenant_id, risk_id, body.evidence_id)


@router.delete("/risks/{risk_id}/evidence/{evidence_id}")
def detach_risk_evidence(risk_id: str, evidence_id: str,
                         user: Principal = Depends(require("risks", "edit"))):
    # `edit`, not `delete`: detaching removes a RELATIONSHIP, not a record, and an Editor
    # (every action except delete) must be able to undo their own attachment.
    return _detach_evidence("risk_evidence", "risks", "risk_id", "risk",
                            user.tenant_id, risk_id, evidence_id)


@router.post("/incidents/{incident_id}/evidence", status_code=201)
def attach_incident_evidence(incident_id: str, body: EvidenceLinkIn,
                             user: Principal = Depends(require("incidents", "edit"))):
    return _attach_evidence("incident_evidence", "incidents", "incident_id", "incident",
                            user.tenant_id, incident_id, body.evidence_id)


@router.delete("/incidents/{incident_id}/evidence/{evidence_id}")
def detach_incident_evidence(incident_id: str, evidence_id: str,
                             user: Principal = Depends(require("incidents", "edit"))):
    return _detach_evidence("incident_evidence", "incidents", "incident_id", "incident",
                            user.tenant_id, incident_id, evidence_id)


# ---------------------------------------------------------------- incident timeline (P4-S7)

INCIDENT_EVENT_TYPES = {"DETECTED", "CONTAINMENT", "INVESTIGATION", "CORRECTIVE_ACTION",
                        "COMMUNICATION", "COMMENT", "RESOLVED", "CLOSED"}


class IncidentEventIn(StrictModel):
    event_type: str = "COMMENT"
    body: str
    occurred_at: IsoDate = None


@router.post("/incidents/{incident_id}/events", status_code=201)
def add_incident_event(incident_id: str, body: IncidentEventIn,
                       user: Principal = Depends(require("incidents", "edit"))):
    """Append to the incident's timeline.

    Gated on `edit`, not `view`: a timeline entry becomes part of the official incident
    record a bank auditor reads, so a read-only Viewer must not be able to write into it.

    Entries are immutable (a deny_update trigger) — the UI offers "add a correction",
    never an edit pencil, because tamper-evidence is the point.
    """
    if body.event_type not in INCIDENT_EVENT_TYPES:
        raise HTTPException(
            400, f"event_type must be one of {', '.join(sorted(INCIDENT_EVENT_TYPES))}")
    if not (body.body or "").strip():
        raise HTTPException(400, "an event needs a body")
    eid = str(uuid.uuid4())
    with engine.begin() as conn:
        _get(conn, "incidents", user.tenant_id, incident_id, "incident")
        conn.execute(insert(t("incident_events")).values(
            id=eid, incident_id=incident_id, event_type=body.event_type,
            body=body.body.strip(), author_user_id=user.user_id, author_kind="member",
            occurred_at=body.occurred_at or now_iso(), created_at=now_iso()))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="incident.event_added", entity_type="incident",
                 entity_id=incident_id, detail={"event_type": body.event_type})
    return {"id": eid}


# ══════════════════════════════════════════════════════════ bulk import (P5-S5)
#
# One generic set of endpoints serves all five registers, driven by
# `api/register_imports.SPECS`. Writing five near-identical routes would guarantee they
# drift; the spec table is also what feeds the template and the mapping UI, so a column
# cannot exist in one and not the others.
#
# Paths are `/import/{register}/…`, NOT `/{register}/import…`. The latter reads better and is
# wrong: FastAPI matches in declaration order, and `/risks/{risk_id}` is declared far earlier
# in this file, so `/risks/import-columns` would resolve to it with risk_id="import-columns"
# and 404 as "risk not found". A literal first segment cannot be shadowed.

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _require_permission(user: Principal, module: str, action: str) -> None:
    """The imperative form of `permissions.require`. The dependency factory cannot be used
    here because the module being checked is decided by a PATH parameter, not at import
    time — one route serves all five registers."""
    if (module, action) not in load_permissions(user):
        raise HTTPException(403, f"You do not have permission to {action} "
                                 f"{MODULE_LABELS.get(module, module)}.")


def _spec(register: str) -> dict:
    spec = register_imports.SPECS.get(register)
    if spec is None:
        raise HTTPException(404, f"no import for {register!r} — "
                                 f"try: {', '.join(sorted(register_imports.SPECS))}")
    return spec


@router.get("/import/{register}/columns")
def import_columns(register: str, user: Principal = Depends(get_current_user)):
    """What the mapping UI renders, and what the template contains. Served rather than
    duplicated in the SPA so the two can never disagree."""
    spec = _spec(register)
    return {"noun": spec["noun"],
            "columns": [{k: c[k] for k in ("key", "label", "help", "required")}
                        for c in spec["columns"]]}


@router.get("/import/{register}/template.xlsx")
def import_template(register: str, user: Principal = Depends(get_current_user)):
    spec = _spec(register)
    data = xlsx_io.build_template(spec["columns"], sheet_name=spec["noun"])
    return Response(data, media_type=XLSX_MIME, headers={
        "Content-Disposition": f'attachment; filename="{register}-import-template.xlsx"'})


@router.post("/import/{register}")
async def import_register(
    register: str,
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON {our_field: their_header}"),
    user: Principal = Depends(get_current_user),
):
    """Import a .xlsx or .csv into one of the registers.

    `mapping` comes from the UI's column-mapping table. When it is omitted we fall back to
    matching the template's own labels, so a downloaded-and-filled template just works — but
    we never GUESS beyond that. Silently mapping "Owner Name" onto `owner` because it looks
    close is how a bulk import quietly puts data in the wrong column.
    """
    spec = _spec(register)
    # Gated on the register's own add permission, not a blanket import right: whoever can
    # create one risk can create fifty, and nobody else can.
    _require_permission(user, spec["module"], "add")

    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB limit")
    try:
        headers, rows = xlsx_io.read_rows(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception:                                            # noqa: BLE001
        raise HTTPException(400, "could not read that file — is it a .xlsx or .csv?") from None

    if not rows:
        raise HTTPException(400, "that file has a header row but no data rows")

    if mapping:
        try:
            user_map = json.loads(mapping)
        except json.JSONDecodeError:
            raise HTTPException(400, "mapping must be JSON") from None
        if not isinstance(user_map, dict):
            raise HTTPException(400, "mapping must be a JSON object")
        known = {c["key"] for c in spec["columns"]}
        col_map = {k: v for k, v in user_map.items() if k in known and v in headers}
    else:
        col_map = {c["key"]: c["label"] for c in spec["columns"] if c["label"] in headers}

    required = [c for c in spec["columns"] if c.get("required") and c["key"] not in col_map]
    if required:
        raise HTTPException(400, "map a column to " +
                            ", ".join(c["label"] for c in required))

    with engine.begin() as conn:
        result = importer.import_rows(
            conn, tenant_id=user.tenant_id, table=spec["table"], rows=rows,
            mapping=col_map, build=spec["build"], label_key=spec["label_key"],
            friendly=_import_friendly)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action=f"{register}.imported", entity_type=spec["table"], entity_id=None,
                 detail={"created": result["created"], "failed": result["failed"]})
    return result


def _import_friendly(e: Exception) -> str:
    """Postgres constraint noise -> something a person can act on. Anything unrecognised is
    truncated rather than dropped: an opaque message still beats a silent failure."""
    s = str(getattr(e, "orig", e))
    if "duplicate key" in s:
        return "a record with that reference already exists"
    if "violates check constraint" in s:
        return "a value is not one this register allows"
    if "violates foreign key" in s:
        return "references something that is not in this organisation"
    return s.split("\n")[0][:200]
