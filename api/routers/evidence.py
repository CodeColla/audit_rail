"""Evidence vault — typed, dated artifacts linked to controls (M3)."""

from __future__ import annotations

import uuid

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse
from sqlalchemy import delete as sqldelete, func, insert, select

from api import activity, storage
from api.auth import Principal, get_current_user, require_roles
from api.config import settings
from api.database import engine, get_conn, t
from api.util import IsoDate, evidence_status, now_iso, today_iso

router = APIRouter(prefix="/evidence", tags=["evidence"])


def _link_counts(conn) -> dict:
    ec = t("evidence_controls")
    return dict(conn.execute(
        select(ec.c.evidence_id, func.count()).group_by(ec.c.evidence_id)
    ).all())


@router.get("")
def list_evidence(
    expiring: bool = Query(False, description="only items expired or expiring soon"),
    user: Principal = Depends(get_current_user),
    conn=Depends(get_conn),
):
    ev = t("evidence")
    counts = _link_counts(conn)
    today = today_iso()
    rows = conn.execute(
        select(ev).where(ev.c.tenant_id == user.tenant_id)
        .order_by(ev.c.valid_until.is_(None), ev.c.valid_until)
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
    evidence_id: str, user: Principal = Depends(get_current_user), conn=Depends(get_conn)
):
    ev, ec, controls = t("evidence"), t("evidence_controls"), t("controls")
    row = conn.execute(
        select(ev).where(ev.c.id == evidence_id, ev.c.tenant_id == user.tenant_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "evidence not found")
    linked = conn.execute(
        select(controls.c.id, controls.c.code, controls.c.statement)
        .join(ec, ec.c.control_id == controls.c.id)
        .where(ec.c.evidence_id == evidence_id)
    ).mappings().all()
    return {**dict(row), "status": evidence_status(row["valid_until"], today_iso()),
            "linked_controls": [dict(c) for c in linked]}


@router.post("", status_code=201)
async def upload_evidence(
    title: str = Form(...),
    evidence_type: str = Form(...),
    issued_at: IsoDate = Form(None),
    valid_until: IsoDate = Form(None),
    control_ids: str | None = Form(None, description="comma-separated control ids"),
    file: UploadFile = File(...),
    user: Principal = Depends(get_current_user),
):
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
    evidence_id: str, user: Principal = Depends(get_current_user), conn=Depends(get_conn)
):
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
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream",
                        filename=row["original_name"])


@router.delete("/{evidence_id}", status_code=204)
def delete_evidence(
    evidence_id: str,
    user: Principal = Depends(require_roles("admin", "manager")),
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
        conn.execute(sqldelete(ev).where(ev.c.id == evidence_id))
        if row["file_id"]:
            conn.execute(sqldelete(files).where(files.c.id == row["file_id"]))
    if key:
        storage.delete(key)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="evidence.deleted", entity_type="evidence", entity_id=evidence_id)
