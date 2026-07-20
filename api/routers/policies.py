"""Policy register — owned policies with versioned files and a review cadence (M3)."""

from __future__ import annotations

import uuid

from fastapi import (APIRouter, Depends, File, Form, HTTPException, UploadFile)
from pydantic import BaseModel
from sqlalchemy import delete as sqldelete, insert, select

from api import activity, storage
from api.auth import Principal, get_current_user, require_roles
from api.database import engine, get_conn, t
from api.util import IsoDate, add_months, now_iso, review_status, today_iso

router = APIRouter(prefix="/policies", tags=["policies"])


class PolicyIn(BaseModel):
    title: str
    description: str | None = None
    owner_member_id: str | None = None
    review_cadence_months: int | None = 12
    next_review_at: IsoDate = None


@router.get("")
def list_policies(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    pol = t("policies")
    today = today_iso()
    rows = conn.execute(
        select(pol).where(pol.c.tenant_id == user.tenant_id)
        .order_by(pol.c.next_review_at.is_(None), pol.c.next_review_at)
    ).mappings()
    return [{**dict(r), "review_status": review_status(r["next_review_at"], today)}
            for r in rows]


@router.post("", status_code=201)
def create_policy(body: PolicyIn, user: Principal = Depends(get_current_user)):
    pid, now = str(uuid.uuid4()), now_iso()
    with engine.begin() as conn:
        conn.execute(insert(t("policies")).values(
            id=pid, tenant_id=user.tenant_id, title=body.title,
            description=body.description, owner_member_id=body.owner_member_id,
            review_cadence_months=body.review_cadence_months,
            next_review_at=body.next_review_at, status="active", created_at=now))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="policy.created", entity_type="policy", entity_id=pid)
    return {"id": pid}


@router.get("/{policy_id}")
def policy_detail(
    policy_id: str, user: Principal = Depends(get_current_user), conn=Depends(get_conn)
):
    pol, pv, files = t("policies"), t("policy_versions"), t("files")
    row = conn.execute(
        select(pol).where(pol.c.id == policy_id, pol.c.tenant_id == user.tenant_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "policy not found")
    versions = conn.execute(
        select(pv, files.c.original_name, files.c.size_bytes)
        .join(files, pv.c.file_id == files.c.id, isouter=True)
        .where(pv.c.policy_id == policy_id)
        .order_by(pv.c.effective_from.desc().nullslast(), pv.c.created_at.desc())
    ).mappings().all()
    return {**dict(row), "review_status": review_status(row["next_review_at"], today_iso()),
            "versions": [dict(v) for v in versions]}


@router.post("/{policy_id}/versions", status_code=201)
async def add_version(
    policy_id: str,
    version_label: str = Form(...),
    effective_from: IsoDate = Form(None),
    approved_by_member_id: str | None = Form(None),
    notes: str | None = Form(None),
    roll_review: bool = Form(True, description="advance next_review_at by the cadence"),
    file: UploadFile = File(...),
    user: Principal = Depends(get_current_user),
):
    pol = t("policies")
    with engine.begin() as conn:
        p = conn.execute(
            select(pol).where(pol.c.id == policy_id, pol.c.tenant_id == user.tenant_id)
        ).mappings().first()
        if p is None:
            raise HTTPException(404, "policy not found")
        data = await file.read()
        key, sha, size = storage.save(user.tenant_id, data, file.filename or "policy")
        file_id, ver_id, now = str(uuid.uuid4()), str(uuid.uuid4()), now_iso()
        members = t("tenant_members")
        member_id = conn.execute(
            select(members.c.id).where(members.c.tenant_id == user.tenant_id,
                                       members.c.user_id == user.user_id)
        ).scalar()
        conn.execute(insert(t("files")).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "policy", mime_type=file.content_type,
            size_bytes=size, sha256=sha, uploaded_by_member_id=member_id, created_at=now))
        conn.execute(insert(t("policy_versions")).values(
            id=ver_id, policy_id=policy_id, version_label=version_label, file_id=file_id,
            approved_by_member_id=approved_by_member_id, effective_from=effective_from,
            notes=notes, created_at=now))
        new_review = None
        if roll_review and p["review_cadence_months"]:
            new_review = add_months(today_iso(), p["review_cadence_months"])
            conn.execute(pol.update().where(pol.c.id == policy_id)
                         .values(next_review_at=new_review))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="policy.version_added", entity_type="policy", entity_id=policy_id,
                 detail={"version": version_label, "sha256": sha})
    return {"version_id": ver_id, "file_id": file_id, "next_review_at": new_review}


@router.post("/{policy_id}/review")
def mark_reviewed(policy_id: str, user: Principal = Depends(get_current_user)):
    """Record a review: roll next_review_at forward by the policy's cadence."""
    pol = t("policies")
    with engine.begin() as conn:
        p = conn.execute(
            select(pol).where(pol.c.id == policy_id, pol.c.tenant_id == user.tenant_id)
        ).mappings().first()
        if p is None:
            raise HTTPException(404, "policy not found")
        months = p["review_cadence_months"] or 12
        new_review = add_months(today_iso(), months)
        conn.execute(pol.update().where(pol.c.id == policy_id)
                     .values(next_review_at=new_review))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="policy.reviewed", entity_type="policy", entity_id=policy_id,
                 detail={"next_review_at": new_review})
    return {"next_review_at": new_review, "review_status": "ok"}


@router.delete("/{policy_id}", status_code=204)
def delete_policy(
    policy_id: str, user: Principal = Depends(require_roles("admin", "manager"))
):
    pol, pv, files = t("policies"), t("policy_versions"), t("files")
    with engine.begin() as conn:
        if conn.execute(select(pol.c.id).where(
                pol.c.id == policy_id, pol.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "policy not found")
        file_ids = list(conn.execute(
            select(pv.c.file_id).where(pv.c.policy_id == policy_id)).scalars())
        keys = list(conn.execute(select(files.c.storage_key).where(
            files.c.id.in_([f for f in file_ids if f]))).scalars()) if file_ids else []
        conn.execute(sqldelete(pv).where(pv.c.policy_id == policy_id))
        conn.execute(sqldelete(pol).where(pol.c.id == policy_id))
        for fid in filter(None, file_ids):
            conn.execute(sqldelete(files).where(files.c.id == fid))
    for k in keys:
        storage.delete(k)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="policy.deleted", entity_type="policy", entity_id=policy_id)
