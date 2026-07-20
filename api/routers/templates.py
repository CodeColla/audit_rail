"""Audits › Templates — bank checklists, the import wizard, mapping proposals,
and per-template scoring config (M0 read + M6 import/scoring)."""

from __future__ import annotations

import uuid

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from api import activity, scoring, storage
from api.auth import Principal, get_current_user
from api.database import engine, get_conn, t
from api.mapping import best_control, classify, toks
from api.scoring import config_for_template
from api.util import now_iso
from api.xlsx_io import parse_checklist

router = APIRouter(prefix="/templates", tags=["templates"])


# --------------------------------------------------------------- read

@router.get("")
def list_templates(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    templates, questions = t("templates"), t("questions")
    counts = dict(conn.execute(
        select(questions.c.template_id, func.count()).group_by(questions.c.template_id)
    ).all())
    rows = conn.execute(select(templates).where(templates.c.tenant_id == user.tenant_id)
                        .order_by(templates.c.created_at)).mappings()
    return [{**dict(r), "question_count": counts.get(r["id"], 0)} for r in rows]


@router.get("/{template_id}")
def get_template(template_id: str, user: Principal = Depends(get_current_user),
                 conn=Depends(get_conn)):
    templates, sections = t("templates"), t("template_sections")
    row = conn.execute(select(templates).where(
        templates.c.id == template_id,
        templates.c.tenant_id == user.tenant_id)).mappings().first()
    if not row:
        raise HTTPException(404, "template not found")
    secs = conn.execute(select(sections).where(sections.c.template_id == template_id)
                        .order_by(sections.c.sort_order)).mappings()
    return {**dict(row), "sections": [dict(s) for s in secs]}


@router.get("/{template_id}/questions")
def list_questions(template_id: str, limit: int = Query(200, le=1000), offset: int = 0,
                   user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    questions, sections = t("questions"), t("template_sections")
    q = (select(questions, sections.c.title.label("section_title"))
         .join(sections, questions.c.section_id == sections.c.id, isouter=True)
         .where(questions.c.template_id == template_id)
         .order_by(questions.c.sort_order).limit(limit).offset(offset))
    return [dict(r) for r in conn.execute(q).mappings()]


# --------------------------------------------------------------- import wizard

def _tenant_control_index(conn, tenant_id):
    """{domain_code: [(control_id, token_set)]} over the tenant's active controls."""
    controls, domains = t("controls"), t("domains")
    idx: dict = {}
    for cid, stmt, dcode in conn.execute(
            select(controls.c.id, controls.c.statement, domains.c.code)
            .join(domains, controls.c.domain_id == domains.c.id)
            .where(controls.c.tenant_id == tenant_id, controls.c.status == "active")):
        idx.setdefault(dcode, []).append((cid, toks(stmt)))
    return idx


@router.post("/import", status_code=201)
async def import_checklist(
    bank_name: str = Form(...),
    version_label: str | None = Form(None),
    title: str | None = Form(None),
    sheet: str | None = Form(None),
    question_col: int | None = Form(None),
    number_col: int | None = Form(None),
    section_col: int | None = Form(None),
    header_row: int | None = Form(None),
    file: UploadFile = File(...),
    user: Principal = Depends(get_current_user),
):
    data = await file.read()
    try:
        meta, rows = parse_checklist(data, sheet, question_col, number_col,
                                     section_col, header_row)
    except Exception as e:  # openpyxl / detection failures → 400
        raise HTTPException(400, f"could not parse file: {e}")
    if not rows:
        raise HTTPException(400, "no question rows detected")

    tpl_id, file_id, now = str(uuid.uuid4()), str(uuid.uuid4()), now_iso()
    key, sha, size = storage.save(user.tenant_id, data, file.filename or "checklist.xlsx")
    ctl_idx = None
    with engine.begin() as conn:
        member_id = conn.execute(select(t("tenant_members").c.id).where(
            t("tenant_members").c.tenant_id == user.tenant_id,
            t("tenant_members").c.user_id == user.user_id)).scalar()
        conn.execute(insert(t("files")).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "checklist.xlsx",
            mime_type=file.content_type, size_bytes=size, sha256=sha,
            uploaded_by_member_id=member_id, created_at=now))
        conn.execute(insert(t("templates")).values(
            id=tpl_id, tenant_id=user.tenant_id, bank_name=bank_name,
            title=title or f"{bank_name} checklist", version_label=version_label,
            source_file_id=file_id, status="active",
            notes=f"Imported from {file.filename}", created_at=now))

        # sections (preserve first-seen order), then questions
        section_ids: dict = {}
        for i, row in enumerate(rows):
            sec_title = row["section"] or "General"
            if sec_title not in section_ids:
                sid = str(uuid.uuid4())
                conn.execute(insert(t("template_sections")).values(
                    id=sid, template_id=tpl_id, title=sec_title,
                    sort_order=len(section_ids)))
                section_ids[sec_title] = sid
            conn.execute(insert(t("questions")).values(
                id=str(uuid.uuid4()), template_id=tpl_id,
                section_id=section_ids[sec_title], number=row["number"],
                text=row["text"], sort_order=i))

        # propose control mappings (suggested, confidence-scored)
        ctl_idx = _tenant_control_index(conn, user.tenant_id)
        proposals = 0
        q_rows = conn.execute(
            select(t("questions").c.id, t("questions").c.text,
                   t("template_sections").c.title)
            .join(t("template_sections"),
                  t("questions").c.section_id == t("template_sections").c.id)
            .where(t("questions").c.template_id == tpl_id)).all()
        for qid, text, sec in q_rows:
            dom = classify(text, sec or "")
            cands = ctl_idx.get(dom) or ctl_idx.get("GRP") or []
            cid, score = best_control(text, cands)
            if cid:
                conn.execute(insert(t("question_control_map")).values(
                    id=str(uuid.uuid4()), question_id=qid, control_id=cid,
                    confidence=score, status="suggested", created_at=now))
                proposals += 1
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="template.imported", entity_type="template", entity_id=tpl_id,
                 detail={"bank": bank_name, "questions": meta["row_count"]})
    return {"template_id": tpl_id, "sections": len(section_ids),
            "questions": len(rows), "proposals": proposals, "meta": meta}


@router.get("/{template_id}/proposals")
def list_proposals(template_id: str, user: Principal = Depends(get_current_user),
                   conn=Depends(get_conn)):
    q, qcm, c = t("questions"), t("question_control_map"), t("controls")
    rows = conn.execute(
        select(q.c.id.label("question_id"), q.c.number, q.c.text,
               qcm.c.id.label("mapping_id"), qcm.c.confidence, qcm.c.status,
               c.c.id.label("control_id"), c.c.code, c.c.statement)
        .join(qcm, qcm.c.question_id == q.c.id, isouter=True)
        .join(c, qcm.c.control_id == c.c.id, isouter=True)
        .where(q.c.template_id == template_id)
        .order_by(q.c.sort_order)).mappings().all()
    return [dict(r) for r in rows]


class ConfirmIn(BaseModel):
    confirm_high_confidence: float | None = None   # bulk-confirm ≥ this score
    decisions: list[dict] | None = None            # [{question_id, action, control_id?}]


@router.post("/{template_id}/proposals/confirm")
def confirm_proposals(template_id: str, body: ConfirmIn,
                      user: Principal = Depends(get_current_user)):
    qcm, q = t("question_control_map"), t("questions")
    confirmed = rejected = 0
    with engine.begin() as conn:
        member_id = conn.execute(select(t("tenant_members").c.id).where(
            t("tenant_members").c.tenant_id == user.tenant_id,
            t("tenant_members").c.user_id == user.user_id)).scalar()
        tpl_q = select(q.c.id).where(q.c.template_id == template_id).scalar_subquery()
        if body.confirm_high_confidence is not None:
            res = conn.execute(update(qcm).where(
                qcm.c.question_id.in_(tpl_q), qcm.c.status == "suggested",
                qcm.c.confidence >= body.confirm_high_confidence).values(
                status="confirmed", confirmed_by_member_id=member_id))
            confirmed += res.rowcount or 0
        for d in (body.decisions or []):
            action = d.get("action")
            status_val = {"confirm": "confirmed", "reject": "rejected"}.get(action)
            if not status_val:
                continue
            vals = {"status": status_val, "confirmed_by_member_id": member_id}
            if d.get("control_id"):
                vals["control_id"] = d["control_id"]
            conn.execute(update(qcm).where(
                qcm.c.question_id == d["question_id"],
                qcm.c.question_id.in_(tpl_q)).values(**vals))
            confirmed += status_val == "confirmed"
            rejected += status_val == "rejected"
    return {"confirmed": confirmed, "rejected": rejected}


# --------------------------------------------------------------- scoring config

@router.get("/{template_id}/scoring")
def get_scoring(template_id: str, user: Principal = Depends(get_current_user),
                conn=Depends(get_conn)):
    return config_for_template(conn, template_id)


class ScoringIn(BaseModel):
    config: dict


@router.put("/{template_id}/scoring")
def set_scoring(template_id: str, body: ScoringIn,
                user: Principal = Depends(get_current_user)):
    import json
    sc = t("scoring_configs")
    with engine.begin() as conn:
        if conn.execute(select(t("templates").c.id).where(
                t("templates").c.id == template_id,
                t("templates").c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "template not found")
        exists = conn.execute(select(sc.c.id).where(
            sc.c.template_id == template_id)).scalar()
        payload = json.dumps(body.config)
        if exists:
            conn.execute(update(sc).where(sc.c.id == exists).values(
                config=payload, updated_at=now_iso()))
        else:
            conn.execute(insert(sc).values(
                id=str(uuid.uuid4()), template_id=template_id, config=payload,
                updated_at=now_iso()))
    return {"ok": True}
