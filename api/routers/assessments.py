"""Audit workspace (M4) — assessments, responses, review threads, findings,
and auditor guest access.

Access model:
  - tenant members act within their own tenant (get_current_user).
  - auditor guests are scoped to a single assessment (get_caller + _access),
    can read and add remarks/findings, but cannot edit vendor answers.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete as sqldelete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from api.core import activity, storage
from api.domain import scoring
from api.core.auth import Principal, create_guest_token, get_caller, get_current_user
from api.core.permissions import require
from api.core.database import engine, get_conn, t
from api.core.util import IsoDate, StrictModel, now_iso, today_iso
from api.rendering.xlsx_io import build_answers_workbook

router = APIRouter(prefix="/assessments", tags=["assessments"])


# --------------------------------------------------------------- helpers

def _member_id(conn, tenant_id, user_id):
    m = t("tenant_members")
    return conn.execute(select(m.c.id).where(
        m.c.tenant_id == tenant_id, m.c.user_id == user_id)).scalar()


def _access(conn, caller: Principal, assessment_id: str) -> dict:
    """Return the assessment row if the caller may access it, else raise."""
    a = t("assessments")
    row = conn.execute(select(a).where(a.c.id == assessment_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "assessment not found")
    if caller.kind == "member":
        if row["tenant_id"] != caller.tenant_id:
            raise HTTPException(404, "assessment not found")
    else:  # guest
        if caller.get("assessment_id") != assessment_id:
            raise HTTPException(403, "not authorised for this assessment")
        g = t("assessment_guests")
        grant = conn.execute(select(g).where(
            g.c.assessment_id == assessment_id, g.c.user_id == caller.user_id,
            g.c.revoked_at.is_(None))).mappings().first()
        if grant is None or (grant["expires_at"] and grant["expires_at"] < today_iso()):
            raise HTTPException(403, "guest access revoked or expired")
    return dict(row)


def _best_controls(conn, template_id: str) -> dict:
    """question_id -> best-matching control (confirmed first, then confidence).

    REJECTED mappings are excluded outright, not merely ranked last (P4-S5). Rejecting a
    proposed mapping is a person saying "this bank point is not about that control"; if it
    stayed in the pool, a question whose only mapping was rejected would still show that
    control in the grid AND prefill its stock answer into the audit — putting an answer the
    reviewer explicitly refused in front of a bank auditor.
    """
    qcm, q, c = t("question_control_map"), t("questions"), t("controls")
    rows = conn.execute(
        select(q.c.id.label("qid"), c.c.id.label("cid"), c.c.code, c.c.statement,
               c.c.stock_response, c.c.stock_comment, c.c.na_justification,
               qcm.c.confidence, qcm.c.status)
        .join(qcm, qcm.c.question_id == q.c.id)
        .join(c, qcm.c.control_id == c.c.id)
        # a RETIRED control has stopped answering new audits — its stock answer must not
        # keep prefilling, or grid-displaying as the mapped control (P4-S5)
        .where(q.c.template_id == template_id, qcm.c.status != "rejected",
               c.c.status == "active")
    ).mappings().all()
    best: dict = {}
    for r in sorted(rows, key=lambda x: (0 if x["status"] == "confirmed" else 1,
                                         -(x["confidence"] or 0))):
        best.setdefault(r["qid"], r)
    return best


def _risk(likelihood, impact):
    if not likelihood or not impact:
        return None, None
    s = likelihood * impact
    return s, ("high" if s >= 6 else "medium" if s >= 2 else "low")


def _progress(conn, assessment_id, template_id):
    q, r = t("questions"), t("responses")
    total = conn.execute(select(func.count()).where(
        q.c.template_id == template_id)).scalar()
    values = list(conn.execute(select(r.c.response_value).where(
        r.c.assessment_id == assessment_id, r.c.response_value.isnot(None))).scalars())
    answered = len(values)
    # findings are org-level (one finding can be raised by several banks); reach them
    # through the finding_assessments pivot rather than a column on findings.
    fnd, fa = t("findings"), t("finding_assessments")
    high_open = conn.execute(
        select(func.count(func.distinct(fnd.c.id)))
        .select_from(fnd.join(fa, fa.c.finding_id == fnd.c.id))
        .where(fa.c.assessment_id == assessment_id,
               fnd.c.risk_rating == "high",
               fnd.c.status.in_(("open", "remediation")))).scalar()
    # verdict from the template's scoring config; an open High finding caps it
    result = scoring.evaluate(values, scoring.config_for_template(conn, template_id))
    verdict = "Draft" if total == 0 else result["verdict"]
    if high_open and verdict == "Satisfactory":
        verdict = "Conditional"
    return total, answered, high_open, verdict, result["score_pct"]


# --------------------------------------------------------------- assessments

class AssessmentIn(StrictModel):
    template_id: str
    title: str
    bank_name: str | None = None
    period_start: IsoDate = None
    period_end: IsoDate = None
    bank_spoc_name: str | None = None
    bank_spoc_email: str | None = None


@router.post("", status_code=201)
def create_assessment(body: AssessmentIn, user: Principal = Depends(require("audits", "add"))):
    tmpl = t("templates")
    with engine.begin() as conn:
        tpl = conn.execute(select(tmpl).where(
            tmpl.c.id == body.template_id,
            tmpl.c.tenant_id == user.tenant_id)).mappings().first()
        if tpl is None:
            raise HTTPException(404, "template not found")
        aid = str(uuid.uuid4())
        conn.execute(insert(t("assessments")).values(
            id=aid, tenant_id=user.tenant_id, template_id=body.template_id,
            title=body.title, bank_name=body.bank_name or tpl["bank_name"],
            period_start=body.period_start, period_end=body.period_end,
            status="draft", bank_spoc_name=body.bank_spoc_name,
            bank_spoc_email=body.bank_spoc_email,
            vendor_spoc_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now_iso()))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="assessment.created", entity_type="assessment", entity_id=aid)
    return {"id": aid}


@router.get("")
def list_assessments(q: str | None = Query(None, description="search title and bank name"),
                     user: Principal = Depends(require("audits", "view")), conn=Depends(get_conn)):
    a = t("assessments")
    stmt = select(a).where(a.c.tenant_id == user.tenant_id).order_by(a.c.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(a.c.title).like(like)
                          | func.lower(func.coalesce(a.c.bank_name, "")).like(like))
    rows = conn.execute(stmt).mappings().all()
    out = []
    for r in rows:
        total, answered, _, verdict, _pct = _progress(conn, r["id"], r["template_id"])
        out.append({**dict(r), "total_questions": total, "answered": answered,
                    "predicted_verdict": r["verdict"] or verdict})
    return out


@router.get("/{assessment_id}")
def assessment_detail(assessment_id: str, caller: Principal = Depends(get_caller),
                      conn=Depends(get_conn)):
    row = _access(conn, caller, assessment_id)
    total, answered, high_open, verdict, pct = _progress(conn, assessment_id, row["template_id"])
    return {**row, "total_questions": total, "answered": answered,
            "open_high_findings": high_open, "score_pct": pct,
            "predicted_verdict": row["verdict"] or verdict}


class StatusIn(StrictModel):
    status: str | None = None
    verdict: str | None = None
    verdict_notes: str | None = None


ASSESSMENT_STATUSES = ("draft", "in_progress", "submitted", "in_review",
                       "verdict_issued", "closed")


@router.patch("/{assessment_id}")
def update_assessment(assessment_id: str, body: StatusIn,
                      user: Principal = Depends(require("audits", "edit"))):
    a = t("assessments")
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    # the column has a CHECK; without this a typo'd status surfaced as a raw 500
    if "status" in vals and vals["status"] not in ASSESSMENT_STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(ASSESSMENT_STATUSES)}")
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        conn.execute(update(a).where(a.c.id == assessment_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="assessment.updated", entity_type="assessment",
                 entity_id=assessment_id, detail=vals)
    return {"ok": True}


@router.delete("/{assessment_id}", status_code=204)
def delete_assessment(assessment_id: str,
                      user: Principal = Depends(require("audits", "delete"))):
    """P7-S6a. There was no way to remove an audit at all — not gated behind a permission,
    simply no route. Safe to add without a schema change: every table referencing
    `assessments.id` already `ON DELETE CASCADE`s (responses, findings, guests, the
    response_evidence/documents/incidents/assets links, review threads) except
    `tasks.assessment_id`, which is `ON DELETE SET NULL` — a task a closed audit raised
    outlives the audit that raised it, on purpose (see that FK's own comment in
    db/schema.sql). One statement does the whole thing; nothing here needs to walk the
    child tables by hand.

    **Also removes the imported checklist behind it, if nothing else uses it any more**
    (reported by Sumit: a deleted audit kept showing up in Checklist mappings and the Bank
    crosswalk). `templates` is a REUSABLE thing by schema design — `assessments.template_id`
    has no ON DELETE action specifically so a bank's checklist can outlive any one year's
    audit — but nothing in the product actually creates a second assessment from an existing
    template today (the only `POST /assessments` call site is Import.tsx, straight after a
    fresh import), so in practice "delete the audit" and "delete the checklist I just
    imported for it" are the same user intent. Only ever removes the template when THIS was
    the last assessment referencing it — if a future feature does reuse templates, an
    audit still in progress against a shared checklist is never touched.
    """
    a, tpl = t("assessments"), t("templates")
    with engine.begin() as conn:
        row = _access(conn, user, assessment_id)
        template_id = row["template_id"]
        conn.execute(sqldelete(a).where(a.c.id == assessment_id))
        still_used = conn.execute(select(func.count()).select_from(a)
                                  .where(a.c.template_id == template_id)).scalar()
        template_deleted = False
        if not still_used:
            conn.execute(sqldelete(tpl).where(tpl.c.id == template_id))
            template_deleted = True
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="assessment.deleted", entity_type="assessment",
                 entity_id=assessment_id,
                 detail={"bank_name": row.get("bank_name"), "title": row.get("title"),
                        "template_deleted": template_deleted})


@router.post("/{assessment_id}/prefill")
def prefill(assessment_id: str, user: Principal = Depends(require("audits", "edit"))):
    """Answer once: fill responses from mapped controls' stock answers."""
    with engine.begin() as conn:
        row = _access(conn, user, assessment_id)
        best = _best_controls(conn, row["template_id"])
        resp = t("responses")
        # A response row with response_value IS NULL is a PLACEHOLDER — post_message opens
        # one when an auditor asks about a question nobody has answered yet, purely so the
        # thread has somewhere to hang off. Treating "has a row" as "has an answer" meant
        # that placeholder blocked prefill for that question forever (P4-S5): once an
        # auditor asked before anyone answered, the question could never be prefilled.
        placeholders = dict(conn.execute(select(resp.c.question_id, resp.c.id).where(
            resp.c.assessment_id == assessment_id, resp.c.response_value.is_(None))).all())
        existing = set(conn.execute(select(resp.c.question_id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.response_value.isnot(None))).scalars())
        n = 0
        for qid, ctl in best.items():
            if qid in existing or not ctl["stock_response"]:
                continue
            if qid in placeholders:
                rid = placeholders[qid]
                conn.execute(update(resp).where(resp.c.id == rid).values(
                    response_value=ctl["stock_response"], comment=ctl["stock_comment"],
                    na_justification=ctl["na_justification"], workflow_status="answered",
                    prefilled_from_control_id=ctl["cid"],
                    updated_by_user_id=user.user_id, updated_at=now_iso()))
            else:
                rid = str(uuid.uuid4())
                conn.execute(insert(resp).values(
                    id=rid, assessment_id=assessment_id, question_id=qid,
                    response_value=ctl["stock_response"], comment=ctl["stock_comment"],
                    na_justification=ctl["na_justification"],
                    workflow_status="answered",
                    prefilled_from_control_id=ctl["cid"],
                    updated_by_user_id=user.user_id, updated_at=now_iso()))
            conn.execute(insert(t("response_revisions")).values(
                id=str(uuid.uuid4()), response_id=rid, rev_no=1,
                response_value=ctl["stock_response"], comment=ctl["stock_comment"],
                author_user_id=user.user_id, created_at=now_iso()))
            n += 1
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="assessment.prefilled", entity_type="assessment",
                 entity_id=assessment_id, detail={"prefilled": n})
    return {"prefilled": n}


class MappingIn(StrictModel):
    control_id: str


@router.patch("/{assessment_id}/responses/{question_id}/mapping")
def remap_question(assessment_id: str, question_id: str, body: MappingIn,
                   user: Principal = Depends(require("audits", "edit"))):
    """Point one audit question at a different standard control.

    Deliberately its OWN endpoint, not a call into
    POST /templates/{id}/proposals/confirm — question_control_map is keyed by question_id,
    which belongs to the TEMPLATE, not this assessment. Routing a mid-assessment "fix this
    one point" action through the template-level endpoint would silently remap the
    question for every other assessment ever run against that template too.

    Never re-prefills or rewrites an already-saved response — response_revisions is an
    audit trail and a bank auditor may already have read the old answer. Mirrors the
    identical "report, don't rewrite" precedent PATCH /library/controls already uses for
    stock_response.
    """
    with engine.begin() as conn:
        row = _access(conn, user, assessment_id)
        ctrl = t("controls")
        if conn.execute(select(ctrl.c.id).where(
                ctrl.c.id == body.control_id,
                ctrl.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "control not found in this organisation")
        q = t("questions")
        if conn.execute(select(q.c.id).where(
                q.c.id == question_id, q.c.template_id == row["template_id"])).first() is None:
            raise HTTPException(404, "question not found")
        qcm = t("question_control_map")
        member_id = _member_id(conn, user.tenant_id, user.user_id)
        # Supersede whatever this question was mapped to before. Without this the old row
        # SURVIVES as a competing mapping, and _best_controls — which ranks confirmed rows
        # by confidence — keeps preferring it, because an importer-proposed mapping has a
        # real confidence score while a hand-picked one has none. The remap would silently
        # do nothing. 'rejected' is the right marker: it is already the status
        # _best_controls excludes, and it preserves the history rather than deleting it.
        conn.execute(update(qcm).where(
            qcm.c.question_id == question_id,
            qcm.c.control_id != body.control_id,
            qcm.c.status != "rejected").values(
            status="rejected", confirmed_by_member_id=member_id))
        try:
            existing = conn.execute(select(qcm.c.question_id).where(
                qcm.c.question_id == question_id,
                qcm.c.control_id == body.control_id)).first()
            if existing:
                conn.execute(update(qcm).where(
                    qcm.c.question_id == question_id,
                    qcm.c.control_id == body.control_id
                ).values(status="confirmed", confirmed_by_member_id=member_id))
            else:
                conn.execute(insert(qcm).values(
                    id=str(uuid.uuid4()), question_id=question_id, control_id=body.control_id,
                    status="confirmed", confidence=None, confirmed_by_member_id=member_id,
                    created_at=now_iso()))
        except IntegrityError:
            raise HTTPException(409, "mapping already exists")
        resp = t("responses")
        rr = conn.execute(select(resp.c.prefilled_from_control_id).where(
            resp.c.assessment_id == assessment_id, resp.c.question_id == question_id,
            resp.c.response_value.isnot(None))).mappings().first()
        was_prefilled = bool(rr and rr["prefilled_from_control_id"])
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="question.remapped", entity_type="question", entity_id=question_id,
                 detail={"control_id": body.control_id, "assessment_id": assessment_id})
    return {"ok": True, "was_prefilled": was_prefilled}


# --------------------------------------------------------------- questions grid

@router.get("/{assessment_id}/export.xlsx")
def export_answers(assessment_id: str, caller: Principal = Depends(get_caller),
                   conn=Depends(get_conn)):
    """Export the assessment's answers as an xlsx (decision D6: bank's format)."""
    row = _access(conn, caller, assessment_id)
    q, s, resp, re_, ev = (t("questions"), t("template_sections"), t("responses"),
                           t("response_evidence"), t("evidence"))
    responses = {r["question_id"]: r for r in conn.execute(
        select(resp).where(resp.c.assessment_id == assessment_id)).mappings()}
    # evidence title + filename per response. Was unscoped — walked EVERY
    # response_evidence/evidence row in the database on every export, across every tenant
    # and assessment, relying only on response_id coincidentally matching this
    # assessment's own rows. Not a leak (response_id is a UUID PK, never guessable), but an
    # unbounded full-table scan that grows worse forever as evidence accumulates.
    resp_ids = [r["id"] for r in responses.values()]
    files = t("files")
    ev_files: dict = {}
    if resp_ids:
        for rid, title, medium, orig_name, ext_url in conn.execute(
                select(re_.c.response_id, ev.c.title, ev.c.medium,
                      files.c.original_name, ev.c.external_url)
                .select_from(re_.join(ev, re_.c.evidence_id == ev.c.id)
                               .join(files, ev.c.file_id == files.c.id, isouter=True))
                .where(re_.c.response_id.in_(resp_ids))):
            label = orig_name if medium == "FILE" else (ext_url or title)
            ev_files.setdefault(rid, []).append(f"{title} — {label}" if label else title)
    q_rows = conn.execute(
        select(q, s.c.title.label("section"))
        .join(s, q.c.section_id == s.c.id, isouter=True)
        .where(q.c.template_id == row["template_id"])
        .order_by(q.c.sort_order)).mappings().all()
    out_rows = []
    for r in q_rows:
        rr = responses.get(r["id"])
        out_rows.append({
            "number": r["number"], "section": r["section"], "text": r["text"],
            "response_value": rr["response_value"] if rr else None,
            "comment": rr["comment"] if rr else None,
            "final_status": rr["final_status"] if rr else None,
            "workflow_status": rr["workflow_status"] if rr else "open",
            "evidence": "; ".join(ev_files.get(rr["id"], [])) if rr else "",
        })
    data = build_answers_workbook(row, out_rows)
    fname = f"{(row['bank_name'] or 'assessment').replace(' ', '_')}_answers.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/{assessment_id}/questions")
def questions_grid(assessment_id: str, status: str | None = Query(None),
                   caller: Principal = Depends(get_caller), conn=Depends(get_conn)):
    row = _access(conn, caller, assessment_id)
    q, s, resp, re_, rd = (t("questions"), t("template_sections"), t("responses"),
                          t("response_evidence"), t("response_documents"))
    best = _best_controls(conn, row["template_id"])
    responses = {r["question_id"]: r for r in conn.execute(
        select(resp).where(resp.c.assessment_id == assessment_id)).mappings()}
    # Same unbounded-scan issue as export_answers — scoped to this assessment's responses.
    resp_ids = [r["id"] for r in responses.values()]
    ev_counts = dict(conn.execute(
        select(re_.c.response_id, func.count()).where(re_.c.response_id.in_(resp_ids))
        .group_by(re_.c.response_id)).all()) if resp_ids else {}
    # issue #13: the grid showed an Evidence count but no Documents one, even though a
    # point can carry both — same shape, one table over.
    doc_counts = dict(conn.execute(
        select(rd.c.response_id, func.count()).where(rd.c.response_id.in_(resp_ids))
        .group_by(rd.c.response_id)).all()) if resp_ids else {}
    rows = conn.execute(
        select(q, s.c.title.label("section"))
        .join(s, q.c.section_id == s.c.id, isouter=True)
        .where(q.c.template_id == row["template_id"])
        .order_by(q.c.sort_order)).mappings().all()
    out = []
    for r in rows:
        rr = responses.get(r["id"])
        ctl = best.get(r["id"])
        item = {
            "question_id": r["id"], "number": r["number"], "text": r["text"],
            "section": r["section"],
            "mapped_control": ctl["code"] if ctl else None,
            "mapped_control_statement": ctl["statement"] if ctl else None,
            "response_value": rr["response_value"] if rr else None,
            "workflow_status": rr["workflow_status"] if rr else "open",
            "evidence_count": ev_counts.get(rr["id"], 0) if rr else 0,
            "document_count": doc_counts.get(rr["id"], 0) if rr else 0,
        }
        if status and item["workflow_status"] != status:
            continue
        out.append(item)
    return out


# --------------------------------------------------------------- responses

class ResponseIn(StrictModel):
    response_value: str  # yes | partial | no | na
    comment: str | None = None
    na_justification: str | None = None


@router.put("/{assessment_id}/responses/{question_id}")
def upsert_response(assessment_id: str, question_id: str, body: ResponseIn,
                    user: Principal = Depends(require("audits", "edit"))):
    if body.response_value == "na" and not body.na_justification:
        raise HTTPException(400, "N/A responses require a justification")
    with engine.begin() as conn:
        row = _access(conn, user, assessment_id)
        q = t("questions")
        if conn.execute(select(q.c.id).where(
                q.c.id == question_id, q.c.template_id == row["template_id"])).first() is None:
            raise HTTPException(404, "question not part of this assessment")
        resp = t("responses")
        cur = conn.execute(select(resp).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).mappings().first()
        now = now_iso()
        if cur is None:
            rid = str(uuid.uuid4())
            conn.execute(insert(resp).values(
                id=rid, assessment_id=assessment_id, question_id=question_id,
                response_value=body.response_value, comment=body.comment,
                na_justification=body.na_justification, workflow_status="answered",
                updated_by_user_id=user.user_id, updated_at=now))
            rev_no = 1
        else:
            rid = cur["id"]
            conn.execute(update(resp).where(resp.c.id == rid).values(
                response_value=body.response_value, comment=body.comment,
                na_justification=body.na_justification, workflow_status="answered",
                # A human editing the answer is no longer "the stock answer" — leaving
                # prefilled_from_control_id set would keep counting a hand-written
                # override as stock reuse (the dashboard's coverage metric reads this
                # column), and would attribute the human's words to the control's canned
                # comment if a later PATCH there ever needed to warn about staleness.
                prefilled_from_control_id=None,
                updated_by_user_id=user.user_id, updated_at=now))
            rev_no = (conn.execute(select(func.max(t("response_revisions").c.rev_no))
                                   .where(t("response_revisions").c.response_id == rid)
                                   ).scalar() or 0) + 1
        conn.execute(insert(t("response_revisions")).values(
            id=str(uuid.uuid4()), response_id=rid, rev_no=rev_no,
            response_value=body.response_value, comment=body.comment,
            author_user_id=user.user_id, created_at=now))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="response.updated", entity_type="response", entity_id=rid,
                 detail={"question_id": question_id, "value": body.response_value})
    return {"response_id": rid, "rev_no": rev_no}


@router.get("/{assessment_id}/responses/{question_id}")
def response_detail(assessment_id: str, question_id: str,
                    caller: Principal = Depends(get_caller), conn=Depends(get_conn)):
    row = _access(conn, caller, assessment_id)
    q = t("questions")
    ques = conn.execute(select(q).where(q.c.id == question_id,
                        q.c.template_id == row["template_id"])).mappings().first()
    if ques is None:
        raise HTTPException(404, "question not found")
    resp = t("responses")
    rr = conn.execute(select(resp).where(
        resp.c.assessment_id == assessment_id,
        resp.c.question_id == question_id)).mappings().first()
    best = _best_controls(conn, row["template_id"]).get(question_id)
    revisions, evidence, messages = [], [], []
    if rr:
        rev = t("response_revisions")
        revisions = [dict(x) for x in conn.execute(
            select(rev).where(rev.c.response_id == rr["id"])
            .order_by(rev.c.rev_no.desc())).mappings()]
        ev, re_ = t("evidence"), t("response_evidence")
        evidence = [dict(x) for x in conn.execute(
            select(ev.c.id, ev.c.title, ev.c.evidence_type, ev.c.valid_until)
            .join(re_, re_.c.evidence_id == ev.c.id)
            .where(re_.c.response_id == rr["id"])).mappings()]
    rm = t("review_messages")
    # A question with no response row has no thread of its own. Comparing to None here
    # would render as `response_id IS NULL`, which is the schema's marker for an
    # ASSESSMENT-level remark — so every unanswered question used to show (and absorb)
    # the whole assessment-level conversation.
    messages = [dict(x) for x in conn.execute(
        select(rm).where(rm.c.assessment_id == assessment_id,
                         rm.c.response_id == rr["id"])
        .order_by(rm.c.created_at)).mappings()] if rr else []
    fnd, fa = t("findings"), t("finding_assessments")
    findings = [dict(x) for x in conn.execute(
        select(fnd).select_from(fnd.join(fa, fa.c.finding_id == fnd.c.id))
        .where(fa.c.assessment_id == assessment_id,
               fa.c.response_id == (rr["id"] if rr else None))).mappings()] if rr else []
    # Evidence attached to the QUESTION's mapped control (P4-S5's evidence_controls) is a
    # separate store from evidence attached to this response directly — a distinct key,
    # never merged into `evidence`, so nothing downstream can silently double-count.
    inherited_evidence = []
    inherited_documents = []
    if best:
        ec, ev2 = t("evidence_controls"), t("evidence")
        inherited_evidence = [dict(x) for x in conn.execute(
            select(ev2.c.id, ev2.c.title, ev2.c.evidence_type, ev2.c.valid_until)
            .join(ec, ec.c.evidence_id == ev2.c.id)
            .where(ec.c.control_id == best["cid"])
            .order_by(ev2.c.valid_until.asc().nullslast())).mappings()]
        # Same idea as inherited_evidence, one table over (control_documents, P4-S5's
        # doc-linking counterpart) — a policy attached to the mapped control shows here too,
        # badged "via control", never merged into the direct `documents` list below.
        cd, doc2 = t("control_documents"), t("documents")
        inherited_documents = [dict(x) for x in conn.execute(
            select(doc2.c.id, doc2.c.title, doc2.c.document_type)
            .join(cd, cd.c.document_id == doc2.c.id)
            .where(cd.c.control_id == best["cid"])
            .order_by(doc2.c.title)).mappings()]
    # P7-S1: Document / Incident / Asset, alongside evidence — same shape (a join table off
    # `responses.id`, queried only when a response row exists), so an unanswered question
    # shows none of them rather than raising, exactly like `evidence` above.
    documents = incidents = assets = []
    if rr:
        doc, rd = t("documents"), t("response_documents")
        documents = [dict(x) for x in conn.execute(
            select(doc.c.id, doc.c.title, doc.c.document_type)
            .join(rd, rd.c.document_id == doc.c.id)
            .where(rd.c.response_id == rr["id"])).mappings()]
        inc, ri = t("incidents"), t("response_incidents")
        incidents = [dict(x) for x in conn.execute(
            select(inc.c.id, inc.c.title, inc.c.severity)
            .join(ri, ri.c.incident_id == inc.c.id)
            .where(ri.c.response_id == rr["id"])).mappings()]
        ast, ra = t("assets"), t("response_assets")
        assets = [dict(x) for x in conn.execute(
            select(ast.c.id, ast.c.name, ast.c.asset_type)
            .join(ra, ra.c.asset_id == ast.c.id)
            .where(ra.c.response_id == rr["id"])).mappings()]
    return {
        "question": {"id": ques["id"], "number": ques["number"], "text": ques["text"]},
        "mapped_control": {"id": best["cid"], "code": best["code"], "statement": best["statement"]} if best else None,
        "response": dict(rr) if rr else None,
        "revisions": revisions, "evidence": evidence, "inherited_evidence": inherited_evidence,
        "documents": documents, "inherited_documents": inherited_documents,
        "incidents": incidents, "assets": assets,
        "thread": messages, "findings": findings,
    }


class LinkEvidenceIn(StrictModel):
    evidence_id: str


@router.post("/{assessment_id}/responses/{question_id}/evidence")
def link_evidence(assessment_id: str, question_id: str, body: LinkEvidenceIn,
                  user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "answer the question before linking evidence")
        ev, re_ = t("evidence"), t("response_evidence")
        if conn.execute(select(ev.c.id).where(
                ev.c.id == body.evidence_id,
                ev.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "evidence not found")
        already = conn.execute(select(re_.c.response_id).where(
            re_.c.response_id == rid,
            re_.c.evidence_id == body.evidence_id)).first()
        if not already:
            conn.execute(insert(re_).values(
                response_id=rid, evidence_id=body.evidence_id))
    return {"ok": True}


@router.delete("/{assessment_id}/responses/{question_id}/evidence/{evidence_id}")
def unlink_evidence(assessment_id: str, question_id: str, evidence_id: str,
                    user: Principal = Depends(require("audits", "edit"))):
    # Gated on .edit, not .delete: unlinking removes a RELATIONSHIP, not a record — same
    # convention as unlink_control_evidence (api/routers/library.py) and unlink_control
    # (api/routers/evidence.py). Only detaches a DIRECTLY-linked item; evidence the question
    # sees via its mapped control (inherited_evidence) has no row here to delete at all — see
    # unlink_control_evidence for that path.
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "response not found")
        re_ = t("response_evidence")
        conn.execute(sqldelete(re_).where(
            re_.c.tenant_id == user.tenant_id, re_.c.response_id == rid,
            re_.c.evidence_id == evidence_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="response.evidence_unlinked", entity_type="response", entity_id=rid,
                 detail={"evidence_id": evidence_id})
    return {"ok": True}


# P7-S1. Three near-duplicates of link_evidence, not a generalised "link" route: each targets
# a different table and a different join table (response_documents/incidents/assets), so
# genericising would mean a runtime table-name lookup instead of the boring, obviously-correct
# repetition — small enough here that copying is the more readable choice.

class LinkDocumentIn(StrictModel):
    document_id: str


@router.post("/{assessment_id}/responses/{question_id}/documents")
def link_document(assessment_id: str, question_id: str, body: LinkDocumentIn,
                  user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "answer the question before linking a document")
        doc, rd = t("documents"), t("response_documents")
        if conn.execute(select(doc.c.id).where(
                doc.c.id == body.document_id,
                doc.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "document not found")
        already = conn.execute(select(rd.c.response_id).where(
            rd.c.response_id == rid,
            rd.c.document_id == body.document_id)).first()
        if not already:
            conn.execute(insert(rd).values(response_id=rid, document_id=body.document_id))
    return {"ok": True}


@router.delete("/{assessment_id}/responses/{question_id}/documents/{document_id}")
def unlink_document(assessment_id: str, question_id: str, document_id: str,
                    user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "response not found")
        rd = t("response_documents")
        conn.execute(sqldelete(rd).where(
            rd.c.tenant_id == user.tenant_id, rd.c.response_id == rid,
            rd.c.document_id == document_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="response.document_unlinked", entity_type="response", entity_id=rid,
                 detail={"document_id": document_id})
    return {"ok": True}


class LinkIncidentIn(StrictModel):
    incident_id: str


@router.post("/{assessment_id}/responses/{question_id}/incidents")
def link_incident(assessment_id: str, question_id: str, body: LinkIncidentIn,
                  user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "answer the question before linking an incident")
        inc, ri = t("incidents"), t("response_incidents")
        if conn.execute(select(inc.c.id).where(
                inc.c.id == body.incident_id,
                inc.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "incident not found")
        already = conn.execute(select(ri.c.response_id).where(
            ri.c.response_id == rid,
            ri.c.incident_id == body.incident_id)).first()
        if not already:
            conn.execute(insert(ri).values(response_id=rid, incident_id=body.incident_id))
    return {"ok": True}


@router.delete("/{assessment_id}/responses/{question_id}/incidents/{incident_id}")
def unlink_incident(assessment_id: str, question_id: str, incident_id: str,
                    user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "response not found")
        ri = t("response_incidents")
        conn.execute(sqldelete(ri).where(
            ri.c.tenant_id == user.tenant_id, ri.c.response_id == rid,
            ri.c.incident_id == incident_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="response.incident_unlinked", entity_type="response", entity_id=rid,
                 detail={"incident_id": incident_id})
    return {"ok": True}


class LinkAssetIn(StrictModel):
    asset_id: str


@router.post("/{assessment_id}/responses/{question_id}/assets")
def link_asset(assessment_id: str, question_id: str, body: LinkAssetIn,
               user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "answer the question before linking an asset")
        ast, ra = t("assets"), t("response_assets")
        if conn.execute(select(ast.c.id).where(
                ast.c.id == body.asset_id,
                ast.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "asset not found")
        already = conn.execute(select(ra.c.response_id).where(
            ra.c.response_id == rid,
            ra.c.asset_id == body.asset_id)).first()
        if not already:
            conn.execute(insert(ra).values(response_id=rid, asset_id=body.asset_id))
    return {"ok": True}


@router.delete("/{assessment_id}/responses/{question_id}/assets/{asset_id}")
def unlink_asset(assessment_id: str, question_id: str, asset_id: str,
                 user: Principal = Depends(require("audits", "edit"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        resp = t("responses")
        rid = conn.execute(select(resp.c.id).where(
            resp.c.assessment_id == assessment_id,
            resp.c.question_id == question_id)).scalar()
        if rid is None:
            raise HTTPException(404, "response not found")
        ra = t("response_assets")
        conn.execute(sqldelete(ra).where(
            ra.c.tenant_id == user.tenant_id, ra.c.response_id == rid,
            ra.c.asset_id == asset_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="response.asset_unlinked", entity_type="response", entity_id=rid,
                 detail={"asset_id": asset_id})
    return {"ok": True}


# --------------------------------------------------------------- review threads

class MessageIn(StrictModel):
    kind: str  # remark | ask | action | validation
    body: str
    question_id: str | None = None


@router.post("/{assessment_id}/messages", status_code=201)
def post_message(assessment_id: str, body: MessageIn,
                 caller: Principal = Depends(get_caller)):
    if body.kind not in ("remark", "ask", "action", "validation"):
        raise HTTPException(400, "invalid message kind")
    with engine.begin() as conn:
        row = _access(conn, caller, assessment_id)
        resp_id = None
        if body.question_id:
            resp_id = conn.execute(select(t("responses").c.id).where(
                t("responses").c.assessment_id == assessment_id,
                t("responses").c.question_id == body.question_id)).scalar()
            if resp_id is None:
                # No answer yet, but the message IS about this question — an auditor
                # asking before we've responded is normal. Open the response row now so
                # the thread hangs off it; leaving response_id NULL would file this as an
                # assessment-level remark and surface it on every other question.
                resp_id = str(uuid.uuid4())
                conn.execute(insert(t("responses")).values(
                    id=resp_id, tenant_id=row["tenant_id"], assessment_id=assessment_id,
                    question_id=body.question_id, workflow_status="open",
                    updated_by_user_id=caller.user_id, updated_at=now_iso()))
        mid = str(uuid.uuid4())
        conn.execute(insert(t("review_messages")).values(
            id=mid, assessment_id=assessment_id, response_id=resp_id,
            author_user_id=caller.user_id,
            author_kind="auditor" if caller.kind == "guest" else "member",
            kind=body.kind, body=body.body, created_at=now_iso()))
        # workflow transition on the target response
        transition = {"ask": "ask_pending", "action": "actioned",
                      "validation": "validated"}.get(body.kind)
        if resp_id and transition:
            conn.execute(update(t("responses")).where(
                t("responses").c.id == resp_id).values(
                workflow_status=transition, updated_at=now_iso()))
    return {"id": mid}


# --------------------------------------------------------------- findings

class FindingIn(StrictModel):
    title: str
    response_id: str | None = None
    description: str | None = None
    recommendation: str | None = None
    likelihood: int | None = None
    impact: int | None = None


@router.post("/{assessment_id}/findings", status_code=201)
def create_finding(assessment_id: str, body: FindingIn,
                   caller: Principal = Depends(get_caller)):
    with engine.begin() as conn:
        row = _access(conn, caller, assessment_id)
        score, rating = _risk(body.likelihood, body.impact)
        fid, now = str(uuid.uuid4()), now_iso()
        # The finding itself is org-level and has no tenant to inherit from (it has no
        # single parent), so tenant_id comes off the assessment we just authorised.
        conn.execute(insert(t("findings")).values(
            id=fid, tenant_id=row["tenant_id"], title=body.title,
            description=body.description, recommendation=body.recommendation,
            likelihood=body.likelihood, impact=body.impact, risk_score=score,
            risk_rating=rating, status="open", created_at=now, updated_at=now))
        # ...and this is what ties it to *this* bank's audit. Raise the same issue in
        # another assessment later and it adds a row here, not a duplicate finding.
        conn.execute(insert(t("finding_assessments")).values(
            id=str(uuid.uuid4()), finding_id=fid, assessment_id=assessment_id,
            response_id=body.response_id, raised_by_user_id=caller.user_id,
            raised_at=now))
    return {"id": fid, "risk_score": score, "risk_rating": rating}


@router.get("/{assessment_id}/findings")
def list_findings(assessment_id: str, caller: Principal = Depends(get_caller),
                  conn=Depends(get_conn)):
    _access(conn, caller, assessment_id)
    fnd, fa = t("findings"), t("finding_assessments")
    return [dict(x) for x in conn.execute(
        select(fnd, fa.c.response_id, fa.c.raised_by_user_id)
        .select_from(fnd.join(fa, fa.c.finding_id == fnd.c.id))
        .where(fa.c.assessment_id == assessment_id)
        .order_by(fnd.c.risk_score.desc().nullslast())).mappings()]


class FindingPatch(StrictModel):
    status: str | None = None
    owner_member_id: str | None = None
    due_at: IsoDate = None


@router.patch("/{assessment_id}/findings/{finding_id}")
def update_finding(assessment_id: str, finding_id: str, body: FindingPatch,
                   user: Principal = Depends(require("audits", "edit"))):
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    if "status" in vals:
        vals["closed_at"] = now_iso() if vals["status"] in ("closed", "accepted") else None
    vals["updated_at"] = now_iso()
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        fnd, fa = t("findings"), t("finding_assessments")
        # reachable only if this finding is actually linked to this assessment
        linked = conn.execute(
            select(fnd.c.id).select_from(fnd.join(fa, fa.c.finding_id == fnd.c.id))
            .where(fnd.c.id == finding_id, fa.c.assessment_id == assessment_id)).first()
        if linked is None:
            raise HTTPException(404, "finding not found")
        conn.execute(update(fnd).where(fnd.c.id == finding_id).values(**vals))
    return {"ok": True}


# --------------------------------------------------------------- auditor guests

class GuestIn(StrictModel):
    email: str
    full_name: str
    firm: str | None = None
    expires_at: IsoDate = None


@router.post("/{assessment_id}/guests", status_code=201)
def invite_guest(assessment_id: str, body: GuestIn,
                 user: Principal = Depends(require("users", "add"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        users = t("users")
        u = conn.execute(select(users).where(
            users.c.email == body.email.lower().strip())).mappings().first()
        if u is None:
            user_id = str(uuid.uuid4())
            conn.execute(insert(users).values(
                id=user_id, email=body.email.lower().strip(), full_name=body.full_name,
                auth_provider="local", is_platform_admin=0, status="invited",
                created_at=now_iso()))
        else:
            user_id = u["id"]
        g = t("assessment_guests")
        existing = conn.execute(select(g.c.id).where(
            g.c.assessment_id == assessment_id, g.c.user_id == user_id)).scalar()
        gid = existing or str(uuid.uuid4())
        if existing:
            conn.execute(update(g).where(g.c.id == gid).values(
                revoked_at=None, expires_at=body.expires_at, firm=body.firm))
        else:
            conn.execute(insert(g).values(
                id=gid, assessment_id=assessment_id, tenant_id=user.tenant_id,
                user_id=user_id, role="auditor", firm=body.firm,
                invited_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
                invited_at=now_iso(), expires_at=body.expires_at))
    token = create_guest_token(user_id=user_id, assessment_id=assessment_id,
                               expires_at=body.expires_at)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="guest.invited", entity_type="assessment",
                 entity_id=assessment_id, detail={"email": body.email, "firm": body.firm})
    return {"guest_id": gid, "user_id": user_id, "access_token": token}


@router.get("/{assessment_id}/guests")
def list_guests(assessment_id: str, user: Principal = Depends(require("audits", "view")),
                conn=Depends(get_conn)):
    _access(conn, user, assessment_id)
    g, users = t("assessment_guests"), t("users")
    return [dict(x) for x in conn.execute(
        select(g, users.c.email, users.c.full_name)
        .join(users, g.c.user_id == users.c.id)
        .where(g.c.assessment_id == assessment_id)).mappings()]


@router.delete("/{assessment_id}/guests/{guest_id}", status_code=204)
def revoke_guest(assessment_id: str, guest_id: str,
                 user: Principal = Depends(require("users", "delete"))):
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        g = t("assessment_guests")
        if conn.execute(select(g.c.id).where(
                g.c.id == guest_id,
                g.c.assessment_id == assessment_id)).first() is None:
            raise HTTPException(404, "guest not found")
        conn.execute(update(g).where(g.c.id == guest_id).values(revoked_at=now_iso()))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="guest.revoked", entity_type="assessment", entity_id=assessment_id)


# ---------------------------------------------------------------- guest evidence (P4-S8)

@router.get("/{assessment_id}/evidence/{evidence_id}/file")
def assessment_evidence_file(assessment_id: str, evidence_id: str,
                             caller: Principal = Depends(get_caller), conn=Depends(get_conn)):
    """Serve evidence bytes to anyone who may see this assessment — including an auditor guest.

    Why this route exists at all. `GET /evidence/{id}/file` is gated on
    `require("evidence","view")`, which depends on `get_current_user`, which raises
    403 "Member access required" for any caller whose kind is not `member` (api/auth.py).
    So an invited bank auditor could see the *titles* of the evidence behind every answer
    and could not open a single one — the whole point of the portal.

    Why it is NOT a guest branch inside api/routers/evidence.py. That router is the
    tenant's whole vault. A guest-tolerant helper in there is one careless `Depends` away
    from leaking it, and every other S8 endpoint (PATCH, control attach, upload) would
    inherit the branch. The guest's entire read surface is this one route.

    The rule, exactly:

        a caller may read the bytes of evidence E through assessment A iff E is attached
        to a RESPONSE belonging to A, and the caller may access A.

    `_access` supplies the second half and is the only thing consulted about the caller —
    never `caller["assessment_id"]`, which is an unverified token claim, and never
    `caller.tenant_id`, which is None for a guest.

    What the join deliberately does NOT reach:

    - **The rest of the tenant's vault.** Filtering on `tenant_id` alone would hand the
      bank's auditor every artifact the vendor owns, including other banks' assessments.
    - **Another assessment in the same tenant.** One vendor runs many bank audits at once;
      `tenant_id` does not separate them, `responses.assessment_id` does.
    - **Control-inherited evidence.** `response_detail` also returns `inherited_evidence`,
      reached through `evidence_controls`. Those links are ORG-level — an artifact attached
      to control X is visible from every assessment mapped to X, including a different
      bank's. Titles already travel that way (a pre-existing decision); bytes must not.
    """
    a = _access(conn, caller, assessment_id)
    ev, re_, resp, files = t("evidence"), t("response_evidence"), t("responses"), t("files")
    row = conn.execute(
        select(files.c.storage_key, files.c.original_name, files.c.mime_type)
        .select_from(
            files
            .join(ev, (ev.c.file_id == files.c.id) & (ev.c.tenant_id == files.c.tenant_id))
            .join(re_, (re_.c.evidence_id == ev.c.id) & (re_.c.tenant_id == ev.c.tenant_id))
            .join(resp, (resp.c.id == re_.c.response_id) & (resp.c.tenant_id == re_.c.tenant_id)))
        .where(ev.c.id == evidence_id,
               ev.c.tenant_id == a["tenant_id"],        # from the assessment row, not the token
               resp.c.assessment_id == assessment_id)   # the path id, already validated above
    ).mappings().first()
    # 404, never 403: a 403 here would confirm that an id exists, turning this into a
    # vault-enumeration oracle for a guest who is allowed to probe it all day.
    if row is None:
        raise HTTPException(404, "file not found")
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "file missing from vault")
    name = (row["original_name"] or "evidence").replace('"', "")
    # No `disposition` parameter. `inline` feeds FilePreview, whose xlsx branch renders
    # workbook content into innerHTML — not a surface to hand an outside auditor.
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
