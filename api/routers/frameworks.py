"""Certification frameworks, their clauses, and readiness (P5-S9 Slice B).

See `api/frameworks.py` for the model and the licensing constraint. In short: a **clause** is
what an auditor asks, a **control** is what we do, and `control_clause_map` is many-to-many so
one control can answer ISO, SOC 2 and RBI at once.

Gated on the existing **`controls`** permission module rather than a new one: frameworks are
part of the control library, and adding a module to `api/permissions.py` re-shapes every role
in every organisation for what is really the same surface.
"""

from __future__ import annotations

import uuid

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from api import activity, importer, xlsx_io
from api.config import settings
from api.auth import Principal
from api.database import engine, get_conn, t
from api.permissions import require
from api.util import StrictModel, evidence_status, now_iso, today_iso

router = APIRouter(prefix="/frameworks", tags=["frameworks"])


def _framework(conn, tenant_id: str, framework_id: str) -> dict:
    row = conn.execute(select(t("frameworks")).where(
        t("frameworks").c.id == framework_id,
        t("frameworks").c.tenant_id == tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "framework not found")
    return dict(row)


class FrameworkIn(StrictModel):
    code: str
    name: str
    version: str | None = None


class FrameworkPatch(StrictModel):
    name: str | None = None
    version: str | None = None


class ClauseIn(StrictModel):
    ref: str
    title: str
    description: str | None = None
    sort_order: int | None = None


# ─────────────────────────────────────────────────────────────── frameworks

@router.get("")
def list_frameworks(user: Principal = Depends(require("controls", "view")),
                    conn=Depends(get_conn)):
    """Every framework with its clause count and how much of it is covered.

    "Covered" means a clause has at least one **applicable** control mapped to it. It says
    nothing about whether that control's evidence is current — that is what the readiness view
    is for, and conflating the two would let an organisation read 100% while every piece of
    proof behind it had expired.
    """
    fw, fc, ccm, c = (t("frameworks"), t("framework_clauses"),
                      t("control_clause_map"), t("controls"))
    out = []
    for f in conn.execute(select(fw).where(fw.c.tenant_id == user.tenant_id)
                          .order_by(fw.c.code)).mappings():
        total = conn.execute(select(func.count()).select_from(fc)
                             .where(fc.c.framework_id == f["id"])).scalar() or 0
        covered = conn.execute(
            select(func.count(func.distinct(fc.c.id)))
            .select_from(fc.join(ccm, ccm.c.clause_id == fc.c.id)
                           .join(c, c.c.id == ccm.c.control_id))
            .where(fc.c.framework_id == f["id"],
                   # Scoped explicitly even though the framework is already the caller's:
                   # every other query in this router filters the map by tenant, and a join
                   # that is only *implicitly* safe is the kind that stops being safe when
                   # someone edits the surrounding query.
                   ccm.c.tenant_id == user.tenant_id,
                   c.c.status == "active",
                   c.c.applicability == "applicable")).scalar() or 0
        out.append({**dict(f), "clause_count": total, "covered_count": covered,
                    "coverage_pct": round(100 * covered / total) if total else 0})
    return out


@router.post("", status_code=201)
def create_framework(body: FrameworkIn,
                     user: Principal = Depends(require("controls", "add"))):
    fid, now = str(uuid.uuid4()), now_iso()
    with engine.begin() as conn:
        try:
            conn.execute(insert(t("frameworks")).values(
                id=fid, tenant_id=user.tenant_id, code=body.code.strip(),
                name=body.name.strip(), version=(body.version or "").strip() or None,
                source="IMPORTED", created_at=now, updated_at=now))
        except IntegrityError:
            raise HTTPException(409, f"a framework with the code {body.code!r} already exists")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="framework.created", entity_type="framework", entity_id=fid,
                 detail={"code": body.code})
    return {"id": fid}


@router.patch("/{framework_id}")
def update_framework(framework_id: str, body: FrameworkPatch,
                     user: Principal = Depends(require("controls", "edit"))):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        return {"ok": True}
    with engine.begin() as conn:
        _framework(conn, user.tenant_id, framework_id)
        conn.execute(update(t("frameworks")).where(
            t("frameworks").c.id == framework_id).values(**vals, updated_at=now_iso()))
    return {"ok": True}


@router.delete("/{framework_id}")
def delete_framework(framework_id: str,
                     user: Principal = Depends(require("controls", "delete"))):
    """Remove a framework and its clauses. Controls are untouched.

    `framework_clauses` cascades from the framework, and `control_clause_map` cascades from
    the clause — so the mappings disappear with it, but every control (and all its evidence,
    documents and answers) survives. That asymmetry is the point of the whole design: our
    controls are ours, and a certification is just a lens over them.
    """
    with engine.begin() as conn:
        f = _framework(conn, user.tenant_id, framework_id)
        conn.execute(delete(t("frameworks")).where(
            t("frameworks").c.id == framework_id,
            t("frameworks").c.tenant_id == user.tenant_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="framework.deleted", entity_type="framework", entity_id=framework_id,
                 detail={"code": f["code"]})
    return {"ok": True}


# ─────────────────────────────────────────────────────────────── clauses

@router.get("/{framework_id}/clauses")
def list_clauses(framework_id: str, user: Principal = Depends(require("controls", "view")),
                 conn=Depends(get_conn)):
    _framework(conn, user.tenant_id, framework_id)
    fc, ccm, c = t("framework_clauses"), t("control_clause_map"), t("controls")
    clauses = [dict(r) for r in conn.execute(
        select(fc).where(fc.c.framework_id == framework_id)
        .order_by(fc.c.sort_order, fc.c.ref)).mappings()]

    mapped: dict[str, list[dict]] = {}
    for r in conn.execute(
            select(ccm.c.clause_id, c.c.id, c.c.code, c.c.statement, c.c.applicability)
            .select_from(ccm.join(c, c.c.id == ccm.c.control_id))
            .where(ccm.c.tenant_id == user.tenant_id, c.c.status == "active")).mappings():
        mapped.setdefault(r["clause_id"], []).append({
            "id": r["id"], "code": r["code"], "statement": r["statement"],
            "applicability": r["applicability"]})
    return [{**cl, "controls": mapped.get(cl["id"], [])} for cl in clauses]


@router.post("/{framework_id}/clauses", status_code=201)
def add_clause(framework_id: str, body: ClauseIn,
               user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _framework(conn, user.tenant_id, framework_id)
        cid = str(uuid.uuid4())
        try:
            conn.execute(insert(t("framework_clauses")).values(
                id=cid, tenant_id=user.tenant_id, framework_id=framework_id,
                ref=body.ref.strip(), title=body.title.strip(),
                description=(body.description or "").strip() or None,
                sort_order=body.sort_order or 0))
        except IntegrityError:
            raise HTTPException(409, f"clause {body.ref!r} already exists in this framework")
    return {"id": cid}


@router.delete("/{framework_id}/clauses/{clause_id}")
def delete_clause(framework_id: str, clause_id: str,
                  user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _framework(conn, user.tenant_id, framework_id)
        fc = t("framework_clauses")
        conn.execute(delete(fc).where(fc.c.id == clause_id,
                                      fc.c.tenant_id == user.tenant_id,
                                      fc.c.framework_id == framework_id))
    return {"ok": True}


# ─────────────────────────────────────────────────────────────── readiness

@router.get("/{framework_id}/readiness")
def readiness(framework_id: str, user: Principal = Depends(require("controls", "view")),
              conn=Depends(get_conn)):
    """Per clause: is it covered, and is the proof behind it still current?

    Three states, deliberately distinct — collapsing them is how a compliance dashboard tells
    a comfortable lie:

      * `uncovered`  — no applicable control is mapped. Real gap.
      * `stale`      — a control is mapped, but it has no evidence at all, or the newest has
                       expired. Answered on paper, unprovable in an audit.
      * `covered`    — mapped, applicable, and backed by evidence that has not expired.

    Evidence freshness reuses `api.util.evidence_status` (`valid | expiring | expired |
    no_expiry`), the same helper the control list and the evidence-gap view already use, so
    freshness means one thing across the product. `expiring` still counts as covered — it is a
    warning, not a gap — and `no_expiry` counts too, because plenty of real evidence (a signed
    NDA, a certificate of incorporation) legitimately never expires.
    """
    _framework(conn, user.tenant_id, framework_id)
    fc, ccm, c, ec, ev = (t("framework_clauses"), t("control_clause_map"), t("controls"),
                          t("evidence_controls"), t("evidence"))
    today = today_iso()

    # Newest evidence per control, so a clause reflects its freshest proof. A control ABSENT
    # from this map has no evidence at all — which is a different thing from evidence with no
    # expiry date, and the two must not collapse into one state.
    latest: dict[str, str | None] = {}
    for r in conn.execute(
            select(ec.c.control_id, func.max(ev.c.valid_until).label("valid_until"))
            .select_from(ec.join(ev, ev.c.id == ec.c.evidence_id))
            .where(ec.c.tenant_id == user.tenant_id)
            .group_by(ec.c.control_id)).mappings():
        latest[r["control_id"]] = r["valid_until"]

    def proven(control_id: str) -> bool:
        if control_id not in latest:
            return False                       # nothing attached at all
        return evidence_status(latest[control_id], today) != "expired"

    by_clause: dict[str, list[dict]] = {}
    for r in conn.execute(
            select(ccm.c.clause_id, c.c.id, c.c.code, c.c.statement, c.c.applicability)
            .select_from(ccm.join(c, c.c.id == ccm.c.control_id))
            .where(ccm.c.tenant_id == user.tenant_id, c.c.status == "active")).mappings():
        by_clause.setdefault(r["clause_id"], []).append(dict(r))

    out, tally = [], {"covered": 0, "stale": 0, "uncovered": 0}
    for cl in conn.execute(select(fc).where(fc.c.framework_id == framework_id)
                           .order_by(fc.c.sort_order, fc.c.ref)).mappings():
        controls = [x for x in by_clause.get(cl["id"], [])
                    if x["applicability"] == "applicable"]
        if not controls:
            state = "uncovered"
        elif any(proven(x["id"]) for x in controls):
            state = "covered"
        else:
            state = "stale"
        tally[state] += 1
        out.append({"id": cl["id"], "ref": cl["ref"], "title": cl["title"], "state": state,
                    "controls": [{"id": x["id"], "code": x["code"],
                                  "statement": x["statement"]} for x in controls]})
    return {"clauses": out, "summary": tally, "total": len(out)}


# ─────────────────────────────────────────────────────────────── clause import

#: One spec drives the downloadable template, the mapping UI and the row builder — the same
#: shape `api/register_imports.py` uses, and for the same reason: a column that exists in one
#: and not the others is drift nobody notices until an import silently drops data.
CLAUSE_COLUMNS = [
    {"key": "ref", "label": "Reference", "required": True,
     "help": "the clause number as the standard writes it, e.g. A.8.5 or CC6.1"},
    {"key": "title", "label": "Title", "required": True,
     "help": "the clause's short name"},
    {"key": "description", "label": "Description", "required": False,
     "help": "the full text, if your licence lets you store it"},
]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_clause(mapped: dict, _resolver) -> dict:
    return {
        "ref": importer.require(mapped, "ref", "Reference"),
        "title": importer.require(mapped, "title", "Title"),
        "description": (mapped.get("description") or "").strip() or None,
    }


def _clause_friendly(e: Exception) -> str:
    s = str(getattr(e, "orig", e))
    if "duplicate key" in s:
        return "this framework already has a clause with that reference"
    return s.split("\n")[0][:200]


@router.get("/{framework_id}/import/columns")
def clause_import_columns(framework_id: str,
                          user: Principal = Depends(require("controls", "view")),
                          conn=Depends(get_conn)):
    f = _framework(conn, user.tenant_id, framework_id)
    return {"noun": f"{f['name']} clauses", "columns": CLAUSE_COLUMNS}


@router.get("/{framework_id}/import/template.xlsx")
def clause_import_template(framework_id: str,
                           user: Principal = Depends(require("controls", "view")),
                           conn=Depends(get_conn)):
    f = _framework(conn, user.tenant_id, framework_id)
    data = xlsx_io.build_template(CLAUSE_COLUMNS, sheet_name="Clauses")
    return Response(data, media_type=XLSX_MIME, headers={
        "Content-Disposition": f'attachment; filename="{f["code"]}-clauses-template.xlsx"'})


@router.post("/{framework_id}/import")
async def import_clauses(
    framework_id: str,
    file: UploadFile = File(...),
    mapping: str | None = Form(None, description="JSON {our_field: their_header}"),
    user: Principal = Depends(require("controls", "edit")),
):
    """Bring your own clause list.

    This is the primary path for any standard we do not ship, and the reason we never have to
    bundle licensed text: an organisation with a copy of ISO 27001 or the TSC pastes its own
    wording into a spreadsheet and imports it. Row-level errors are reported individually, so
    one malformed reference does not cost the other four hundred.
    """
    with engine.begin() as conn:
        _framework(conn, user.tenant_id, framework_id)

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
        known = {c["key"] for c in CLAUSE_COLUMNS}
        col_map = {k: v for k, v in user_map.items() if k in known and v in headers}
    else:
        # A downloaded-and-filled template just works. Nothing fuzzier: guessing that
        # "Clause No." means `ref` is how an import quietly fills the wrong column.
        col_map = {c["key"]: c["label"] for c in CLAUSE_COLUMNS if c["label"] in headers}

    missing = [c for c in CLAUSE_COLUMNS if c["required"] and c["key"] not in col_map]
    if missing:
        raise HTTPException(400, "map a column to " + ", ".join(c["label"] for c in missing))

    with engine.begin() as conn:
        # `framework_clauses` has no created_at/updated_at, and framework_id comes from the
        # URL rather than the file — hence timestamps=False and `extra`.
        result = importer.import_rows(
            conn, tenant_id=user.tenant_id, table="framework_clauses", rows=rows,
            mapping=col_map, build=_build_clause, label_key="ref",
            friendly=_clause_friendly, extra={"framework_id": framework_id},
            timestamps=False)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="framework.clauses_imported", entity_type="framework",
                 entity_id=framework_id,
                 detail={"created": result["created"], "failed": result["failed"]})
    return result
