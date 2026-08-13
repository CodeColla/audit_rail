"""Evidence vault — typed, dated artifacts linked to controls (M3)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse
from sqlalchemy import delete as sqldelete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from api.core import activity, storage
from api.core.auth import Principal, get_current_user
from api.core.permissions import require
from api.core.config import settings
from api.core.database import engine, get_conn, t
from api.core.util import IsoDate, StrictModel, evidence_status, now_iso, today_iso

router = APIRouter(prefix="/evidence", tags=["evidence"])


def _link_counts(conn, tenant_id: str) -> dict:
    # evidence_controls' own FKs are composite (evidence_id, tenant_id) and
    # (control_id, tenant_id), so a row here can never point cross-tenant — this was an
    # unfiltered scan, not a leak, but P4-S5 made this table go from empty to real volume
    # on this hot GET /evidence path, and every other tenant-scoped query in this file
    # filters. Same reasoning as the question_control_map queries in library.py.
    ec = t("evidence_controls")
    return dict(conn.execute(
        select(ec.c.evidence_id, func.count())
        .where(ec.c.tenant_id == tenant_id)
        .group_by(ec.c.evidence_id)
    ).all())


def _norm(vals: dict) -> dict:
    """Empty/whitespace strings -> None, so a form that clears a date sends "" and the
    column ends up NULL rather than failing an iso_ts CHECK. Same helper as registers.py;
    deliberately copied rather than imported — a router reaching into another router's
    private helpers is a coupling this codebase has so far avoided."""
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in vals.items()}


def _reject_null_required(vals: dict, required: tuple[str, ...]):
    """A PATCH must not blank a NOT NULL column. 400 beats an uncaught IntegrityError."""
    for k in required:
        if k in vals and vals[k] is None:
            raise HTTPException(400, f"{k.replace('_', ' ')} cannot be empty")


def _is_unique_violation(e: IntegrityError) -> bool:
    """SQLSTATE 23505 only, so an unrelated constraint surfaces as itself."""
    return getattr(getattr(e, "orig", None), "sqlstate", None) == "23505"


def _with_file(stmt, ev, files):
    """Attach the `files` row behind each evidence row.

    OUTER, never inner: `evidence.file_id` is nullable and `ev_medium_matches` explicitly
    permits medium='LINK' (an external_url), state='REQUESTED', and document_version_id-only
    rows. Every artifact scripts/seed_demo.py creates is a LINK — an inner join would make
    the entire seeded vault silently disappear from the list.

    Every column is `.label()`ed because the handler does dict(r) over .mappings(): `files`
    collides with `evidence` on id, tenant_id and created_at, so selecting it wholesale
    would overwrite the evidence row's own id.
    """
    return (stmt.add_columns(files.c.original_name.label("original_name"),
                             files.c.mime_type.label("mime_type"),
                             files.c.size_bytes.label("size_bytes"))
            .select_from(ev.outerjoin(files, (ev.c.file_id == files.c.id)
                                      & (ev.c.tenant_id == files.c.tenant_id))))


@router.get("")
def list_evidence(
    expiring: bool = Query(False, description="only items expired or expiring soon"),
    q: str | None = Query(None, description="search title and notes"),
    user: Principal = Depends(require("evidence", "view")),
    conn=Depends(get_conn),
):
    ev, files = t("evidence"), t("files")
    counts = _link_counts(conn, user.tenant_id)
    today = today_iso()
    stmt = _with_file(select(ev), ev, files).where(ev.c.tenant_id == user.tenant_id)
    if q:
        like = f"%{q.lower()}%"
        # func.lower(...).like(...) is this codebase's search idiom across seven endpoints;
        # `ilike` appears nowhere and introducing it here would fork the pattern.
        # coalesce is load-bearing: notes is nullable, and lower(NULL) LIKE ... is NULL,
        # which would silently drop every row that has no notes from the OR.
        stmt = stmt.where(func.lower(ev.c.title).like(like)
                          | func.lower(func.coalesce(ev.c.notes, "")).like(like))
    rows = conn.execute(
        stmt.order_by(ev.c.valid_until.is_(None), ev.c.valid_until)
    ).mappings()
    out = []
    for r in rows:
        status = evidence_status(r["valid_until"], today)
        if expiring and status not in ("expired", "expiring"):
            continue
        out.append({**dict(r), "status": status,
                    "linked_controls": counts.get(r["id"], 0)})
    return out


@router.get("/{evidence_id}")
def evidence_detail(
    evidence_id: str, user: Principal = Depends(require("evidence", "view")), conn=Depends(get_conn)
):
    ev, ec, controls, files = t("evidence"), t("evidence_controls"), t("controls"), t("files")
    row = conn.execute(
        _with_file(select(ev), ev, files)
        .where(ev.c.id == evidence_id, ev.c.tenant_id == user.tenant_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "evidence not found")
    linked = conn.execute(
        select(controls.c.id, controls.c.code, controls.c.statement)
        .join(ec, ec.c.control_id == controls.c.id)
        .where(ec.c.evidence_id == evidence_id, ec.c.tenant_id == user.tenant_id)
    ).mappings().all()
    return {**dict(row), "status": evidence_status(row["valid_until"], today_iso()),
            "linked_controls": [dict(c) for c in linked]}


class EvidencePatchIn(StrictModel):
    """Four fields, and the omissions are the point.

    file_id, medium, state, external_url, document_version_id, requirement_id and tenant_id
    are all absent, so StrictModel's extra="forbid" turns any attempt to send one into a
    422. That is what keeps a PATCH from tripping ev_fulfilled_has_body or ev_medium_matches
    (db/schema.sql) as an uncaught IntegrityError, i.e. a 500 — you cannot blank the
    external_url of a LINK artifact, or flip a FILE row to LINK, through this endpoint.
    Re-pointing an artifact at different bytes is a re-upload, not an edit.
    """

    title: str | None = None
    evidence_type: str | None = None
    issued_at: IsoDate = None
    valid_until: IsoDate = None


@router.patch("/{evidence_id}")
def update_evidence(evidence_id: str, body: EvidencePatchIn,
                    user: Principal = Depends(require("evidence", "edit"))):
    """Rename and re-date an artifact. Evidence was insert-once/delete-only until now, so
    a typo in a title meant deleting the file and uploading it again."""
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        return {"ok": True}
    _reject_null_required(vals, ("title", "evidence_type"))
    ev = t("evidence")
    with engine.begin() as conn:
        if conn.execute(select(ev.c.id).where(
                ev.c.id == evidence_id, ev.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "evidence not found")
        conn.execute(update(ev).where(
            ev.c.id == evidence_id, ev.c.tenant_id == user.tenant_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.updated", entity_type="evidence", entity_id=evidence_id,
                 detail=vals)
    return {"ok": True}


class ControlLinkIn(StrictModel):
    control_id: str


def _evidence_in_tenant(conn, tenant_id: str, evidence_id: str):
    if conn.execute(select(t("evidence").c.id).where(
            t("evidence").c.id == evidence_id,
            t("evidence").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(404, "evidence not found")


@router.post("/{evidence_id}/controls", status_code=201)
def attach_control(evidence_id: str, body: ControlLinkIn,
                   user: Principal = Depends(require("evidence", "edit"))):
    """Link an artifact to a control from the evidence side.

    The same evidence_controls rows are already writable from the control side
    (library.py's /library/controls/{cid}/evidence). After this the join table has two
    doors with different permissions — evidence.edit here, controls.edit there. That is
    deliberate: the resource named in the path should govern, and a role that curates the
    vault without owning the control library is a real role. It IS a widening, so whoever
    reviews the permission matrix needs to know.
    """
    ec = t("evidence_controls")
    with engine.begin() as conn:
        _evidence_in_tenant(conn, user.tenant_id, evidence_id)
        if conn.execute(select(t("controls").c.id).where(
                t("controls").c.id == body.control_id,
                t("controls").c.tenant_id == user.tenant_id)).first() is None:
            # 400, not 404: the id is a body field, not a path segment, and a cross-tenant
            # id would otherwise reach the composite FK as an uncaught IntegrityError.
            raise HTTPException(400, "control not found in this organisation")
        try:
            conn.execute(insert(ec).values(tenant_id=user.tenant_id,
                                           evidence_id=evidence_id, control_id=body.control_id))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that evidence is already linked to this control")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.control_linked", entity_type="evidence", entity_id=evidence_id,
                 detail={"control_id": body.control_id})
    return {"ok": True}


@router.delete("/{evidence_id}/controls/{control_id}")
def detach_control(evidence_id: str, control_id: str,
                   user: Principal = Depends(require("evidence", "edit"))):
    """Gated on `edit`, not `delete`: unlinking removes a relationship, not a record, and
    an Editor must be able to undo a link they just made. Same reasoning as registers.py."""
    ec = t("evidence_controls")
    with engine.begin() as conn:
        _evidence_in_tenant(conn, user.tenant_id, evidence_id)
        conn.execute(sqldelete(ec).where(ec.c.tenant_id == user.tenant_id,
                                         ec.c.evidence_id == evidence_id,
                                         ec.c.control_id == control_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.control_unlinked", entity_type="evidence",
                 entity_id=evidence_id, detail={"control_id": control_id})
    return {"ok": True}


@router.post("", status_code=201)
async def upload_evidence(
    title: str | None = Form(None, description="defaults to the filename without its extension"),
    evidence_type: str = Form(...),
    issued_at: IsoDate = Form(None),
    valid_until: IsoDate = Form(None),
    control_ids: str | None = Form(None, description="comma-separated control ids"),
    file: UploadFile = File(...),
    user: Principal = Depends(require("evidence", "add")),
):
    # P4-S8: bulk upload names each artifact after its file, so the title stops being
    # mandatory. `.stem` strips only the extension — never the rest of the basename — and
    # the final fallback keeps `title NOT NULL` unviolatable even for a file called ".pdf".
    title = (title or "").strip() or Path(file.filename or "upload").stem or "upload"
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB limit")
    key, sha, size = storage.save(user.tenant_id, data, file.filename or "upload")

    ev_id, file_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = now_iso()
    members = t("tenant_members")
    with engine.begin() as conn:
        member_id = conn.execute(
            select(members.c.id).where(members.c.tenant_id == user.tenant_id,
                                       members.c.user_id == user.user_id)
        ).scalar()
        conn.execute(insert(t("files")).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "upload", mime_type=file.content_type,
            size_bytes=size, sha256=sha, uploaded_by_member_id=member_id, created_at=now))
        conn.execute(insert(t("evidence")).values(
            id=ev_id, tenant_id=user.tenant_id, title=title, evidence_type=evidence_type,
            file_id=file_id, issued_at=issued_at, valid_until=valid_until,
            created_by_member_id=member_id, created_at=now))
        ids = [c.strip() for c in (control_ids or "").split(",") if c.strip()]
        # only link controls that belong to this tenant
        valid_ids = set(conn.execute(
            select(t("controls").c.id).where(
                t("controls").c.tenant_id == user.tenant_id,
                t("controls").c.id.in_(ids) if ids else False)
        ).scalars()) if ids else set()
        for cid in valid_ids:
            conn.execute(insert(t("evidence_controls")).values(
                evidence_id=ev_id, control_id=cid))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.uploaded", entity_type="evidence", entity_id=ev_id,
                 detail={"title": title, "sha256": sha})
    return {"id": ev_id, "file_id": file_id, "sha256": sha, "size_bytes": size,
            "linked_controls": len(valid_ids)}


@router.get("/{evidence_id}/file")
def download_evidence(
    evidence_id: str, disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
    user: Principal = Depends(require("evidence", "view")), conn=Depends(get_conn)
):
    """Serve the artifact. `disposition=inline` lets FilePreview render it in place;
    the default stays `attachment` so existing download links are unchanged."""
    ev, files = t("evidence"), t("files")
    row = conn.execute(
        select(files.c.storage_key, files.c.original_name, files.c.mime_type)
        .join(ev, ev.c.file_id == files.c.id)
        .where(ev.c.id == evidence_id, ev.c.tenant_id == user.tenant_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "file not found")
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "file missing from vault")
    name = (row["original_name"] or "evidence").replace('"', "")
    return FileResponse(
        path, media_type=row["mime_type"] or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'})


@router.delete("/{evidence_id}", status_code=204)
def delete_evidence(
    evidence_id: str,
    user: Principal = Depends(require("evidence", "delete")),
):
    ev, files = t("evidence"), t("files")
    with engine.begin() as conn:
        row = conn.execute(
            select(ev.c.file_id).where(ev.c.id == evidence_id,
                                       ev.c.tenant_id == user.tenant_id)
        ).mappings().first()
        if row is None:
            raise HTTPException(404, "evidence not found")
        key = conn.execute(
            select(files.c.storage_key).where(files.c.id == row["file_id"])
        ).scalar() if row["file_id"] else None
        conn.execute(sqldelete(t("evidence_controls")).where(
            t("evidence_controls").c.evidence_id == evidence_id))
        try:
            conn.execute(sqldelete(ev).where(ev.c.id == evidence_id))
            if row["file_id"]:
                conn.execute(sqldelete(files).where(files.c.id == row["file_id"]))
        except IntegrityError:
            # Something still points at this artifact — a completed task run, a training
            # assignment, a third-party assessment. Those FKs are RESTRICT/NO ACTION on
            # purpose: the proof behind a completed obligation must not silently vanish.
            raise HTTPException(
                409, "This evidence is still referenced (e.g. by a completed task run or "
                     "a vendor assessment) and cannot be deleted.")
    if key:
        storage.delete(key)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.deleted", entity_type="evidence", entity_id=evidence_id)
