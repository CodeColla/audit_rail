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
from pydantic import BaseModel
from sqlalchemy import delete as sqldelete, func, insert, select, update

from api import activity, scoring
from api.auth import (Principal, create_guest_token, get_caller,
                      get_current_user, require_roles)
from api.database import engine, get_conn, t
from api.util import IsoDate, now_iso, today_iso
from api.xlsx_io import build_answers_workbook

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
    """question_id -> best-matching control (confirmed first, then confidence)."""
    qcm, q, c = t("question_control_map"), t("questions"), t("controls")
    rows = conn.execute(
        select(q.c.id.label("qid"), c.c.id.label("cid"), c.c.code, c.c.statement,
               c.c.stock_response, c.c.stock_comment, c.c.na_justification,
               qcm.c.confidence, qcm.c.status)
        .join(qcm, qcm.c.question_id == q.c.id)
        .join(c, qcm.c.control_id == c.c.id)
        .where(q.c.template_id == template_id)
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

class AssessmentIn(BaseModel):
    template_id: str
    title: str
    bank_name: str | None = None
    period_start: IsoDate = None
    period_end: IsoDate = None
    bank_spoc_name: str | None = None
    bank_spoc_email: str | None = None


@router.post("", status_code=201)
def create_assessment(body: AssessmentIn, user: Principal = Depends(get_current_user)):
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
def list_assessments(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    a = t("assessments")
    rows = conn.execute(select(a).where(a.c.tenant_id == user.tenant_id)
                        .order_by(a.c.created_at.desc())).mappings().all()
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


class StatusIn(BaseModel):
    status: str | None = None
    verdict: str | None = None
    verdict_notes: str | None = None


@router.patch("/{assessment_id}")
def update_assessment(assessment_id: str, body: StatusIn,
                      user: Principal = Depends(get_current_user)):
    a = t("assessments")
    vals = {k: v for k, v in body.model_dump().items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    with engine.begin() as conn:
        _access(conn, user, assessment_id)
        conn.execute(update(a).where(a.c.id == assessment_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="assessment.updated", entity_type="assessment",
                 entity_id=assessment_id, detail=vals)
    return {"ok": True}


@router.post("/{assessment_id}/prefill")
def prefill(assessment_id: str, user: Principal = Depends(get_current_user)):
    """Answer once: fill responses from mapped controls' stock answers."""
    with engine.begin() as conn:
        row = _access(conn, user, assessment_id)
        best = _best_controls(conn, row["template_id"])
        resp = t("responses")
        existing = set(conn.execute(select(resp.c.question_id).where(
            resp.c.assessment_id == assessment_id)).scalars())
        n = 0
        for qid, ctl in best.items():
            if qid in existing or not ctl["stock_response"]:
                continue
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
    # evidence titles per response
    ev_titles: dict = {}
    for rid, title in conn.execute(
            select(re_.c.response_id, ev.c.title)
            .join(ev, re_.c.evidence_id == ev.c.id)):
        ev_titles.setdefault(rid, []).append(title)
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
            "evidence": "; ".join(ev_titles.get(rr["id"], [])) if rr else "",
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
    q, s, resp, re_ = (t("questions"), t("template_sections"), t("responses"),
                       t("response_evidence"))
    best = _best_controls(conn, row["template_id"])
    responses = {r["question_id"]: r for r in conn.execute(
        select(resp).where(resp.c.assessment_id == assessment_id)).mappings()}
    ev_counts = dict(conn.execute(
        select(re_.c.response_id, func.count()).group_by(re_.c.response_id)).all())
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
            "response_value": rr["response_value"] if rr else None,
            "workflow_status": rr["workflow_status"] if rr else "open",
            "evidence_count": ev_counts.get(rr["id"], 0) if rr else 0,
        }
        if status and item["workflow_status"] != status:
            continue
        out.append(item)
    return out


# --------------------------------------------------------------- responses

class ResponseIn(BaseModel):
    response_value: str  # yes | partial | no | na
    comment: str | None = None
    na_justification: str | None = None


@router.put("/{assessment_id}/responses/{question_id}")
def upsert_response(assessment_id: str, question_id: str, body: ResponseIn,
                    user: Principal = Depends(get_current_user)):
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
    messages = [dict(x) for x in conn.execute(
        select(rm).where(rm.c.assessment_id == assessment_id,
                         rm.c.response_id == (rr["id"] if rr else None))
        .order_by(rm.c.created_at)).mappings()]
    fnd, fa = t("findings"), t("finding_assessments")
    findings = [dict(x) for x in conn.execute(
        select(fnd).select_from(fnd.join(fa, fa.c.finding_id == fnd.c.id))
        .where(fa.c.assessment_id == assessment_id,
               fa.c.response_id == (rr["id"] if rr else None))).mappings()] if rr else []
    return {
        "question": {"id": ques["id"], "number": ques["number"], "text": ques["text"]},
        "mapped_control": {"code": best["code"], "statement": best["statement"]} if best else None,
        "response": dict(rr) if rr else None,
        "revisions": revisions, "evidence": evidence,
        "thread": messages, "findings": findings,
    }


class LinkEvidenceIn(BaseModel):
    evidence_id: str


@router.post("/{assessment_id}/responses/{question_id}/evidence")
def link_evidence(assessment_id: str, question_id: str, body: LinkEvidenceIn,
                  user: Principal = Depends(get_current_user)):
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


# --------------------------------------------------------------- review threads

class MessageIn(BaseModel):
    kind: str  # remark | ask | action | validation
    body: str
    question_id: str | None = None


@router.post("/{assessment_id}/messages", status_code=201)
def post_message(assessment_id: str, body: MessageIn,
                 caller: Principal = Depends(get_caller)):
    if body.kind not in ("remark", "ask", "action", "validation"):
        raise HTTPException(400, "invalid message kind")
    with engine.begin() as conn:
        _access(conn, caller, assessment_id)
        resp_id = None
        if body.question_id:
            resp_id = conn.execute(select(t("responses").c.id).where(
                t("responses").c.assessment_id == assessment_id,
                t("responses").c.question_id == body.question_id)).scalar()
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

class FindingIn(BaseModel):
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


class FindingPatch(BaseModel):
    status: str | None = None
    owner_member_id: str | None = None
    due_at: IsoDate = None


@router.patch("/{assessment_id}/findings/{finding_id}")
def update_finding(assessment_id: str, finding_id: str, body: FindingPatch,
                   user: Principal = Depends(get_current_user)):
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

class GuestIn(BaseModel):
    email: str
    full_name: str
    firm: str | None = None
    expires_at: IsoDate = None


@router.post("/{assessment_id}/guests", status_code=201)
def invite_guest(assessment_id: str, body: GuestIn,
                 user: Principal = Depends(require_roles("admin", "manager"))):
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
                revoked_at=None, expires_at=body.expires_at))
        else:
            conn.execute(insert(g).values(
                id=gid, assessment_id=assessment_id, user_id=user_id, role="auditor",
                invited_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
                invited_at=now_iso(), expires_at=body.expires_at))
    token = create_guest_token(user_id=user_id, assessment_id=assessment_id,
                               expires_at=body.expires_at)
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="guest.invited", entity_type="assessment",
                 entity_id=assessment_id, detail={"email": body.email, "firm": body.firm})
    return {"guest_id": gid, "user_id": user_id, "access_token": token}


@router.get("/{assessment_id}/guests")
def list_guests(assessment_id: str, user: Principal = Depends(get_current_user),
                conn=Depends(get_conn)):
    _access(conn, user, assessment_id)
    g, users = t("assessment_guests"), t("users")
    return [dict(x) for x in conn.execute(
        select(g, users.c.email, users.c.full_name)
        .join(users, g.c.user_id == users.c.id)
        .where(g.c.assessment_id == assessment_id)).mappings()]


@router.delete("/{assessment_id}/guests/{guest_id}", status_code=204)
def revoke_guest(assessment_id: str, guest_id: str,
                 user: Principal = Depends(require_roles("admin", "manager"))):
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
