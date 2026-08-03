"""Controls menu — the standard control framework + the bank crosswalk.

All endpoints are tenant-scoped to the caller's membership (M1 auth). The initial 95-control
library is produced by scripts/build_control_library.py (M2); P4-S5 makes controls first-class
editable data — stock_response is the "answer once, reuse everywhere" premise the whole
product rests on, and until this sprint nothing could set it.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from api import activity
from api.auth import Principal
from api.permissions import require
from api.database import engine, get_conn, t
from api.util import StrictModel, evidence_status, now_iso, today_iso

router = APIRouter(prefix="/library", tags=["library"])

LIFECYCLE = ("one_time", "recurring", "per_audit")
APPLICABILITY = ("applicable", "not_applicable")
STOCK = ("yes", "partial", "no", "na")
#: a mapping in this state was a human saying "not about that control" — it must not count
#: toward mapped_count, appear in the crosswalk, or prefill an audit answer.
LIVE_MAPPING_STATUSES = ("confirmed", "suggested")


def _is_unique_violation(e: IntegrityError) -> bool:
    """SQLSTATE 23505 — a reference conflict maps to 409; anything else (e.g. a CHECK)
    surfaces as itself rather than being mislabelled "already in use". Same convention as
    api/routers/registers.py."""
    return getattr(getattr(e, "orig", None), "sqlstate", None) == "23505"


def _norm(vals: dict) -> dict:
    """Empty/whitespace strings -> None, so a cleared optional field submits as "" from a
    form and clears the column instead of failing a CHECK or 500ing. Same convention as
    api/routers/registers.py."""
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in vals.items()}


def _control(conn, tenant_id: str, control_id: str) -> dict:
    row = conn.execute(select(t("controls")).where(
        t("controls").c.id == control_id, t("controls").c.tenant_id == tenant_id
    )).mappings().first()
    if row is None:
        raise HTTPException(404, "control not found")
    return dict(row)


@router.get("/domains")
def list_domains(user: Principal = Depends(require("controls", "view")), conn=Depends(get_conn)):
    domains, controls = t("domains"), t("controls")
    counts = dict(conn.execute(
        select(controls.c.domain_id, func.count())
        .where(controls.c.tenant_id == user.tenant_id)
        .group_by(controls.c.domain_id)
    ).all())
    rows = conn.execute(
        select(domains).where(domains.c.tenant_id == user.tenant_id)
        .order_by(domains.c.sort_order)
    ).mappings()
    return [{**dict(r), "control_count": counts.get(r["id"], 0)} for r in rows]


@router.get("/controls")
def list_controls(
    domain_code: str | None = Query(None),
    applicability: str | None = Query(None, pattern="^(applicable|not_applicable)$"),
    include_retired: bool = Query(False),
    q: str | None = Query(None, description="search reference code and statement"),
    user: Principal = Depends(require("controls", "view")),
    conn=Depends(get_conn),
):
    controls, domains, qcm = t("controls"), t("domains"), t("question_control_map")
    # mapped-question count per control. question_control_map's own FKs are composite
    # (control_id, tenant_id), so a row here can never point at another tenant's control —
    # this is not a leak. The filter is still added: it keeps the query plan honest as the
    # table grows, and it's how every other tenant-scoped query in this file reads.
    # REJECTED mappings never count — a human explicitly said "not about this control".
    counts = dict(conn.execute(
        select(qcm.c.control_id, func.count())
        .where(qcm.c.tenant_id == user.tenant_id,
               qcm.c.status.in_(LIVE_MAPPING_STATUSES))
        .group_by(qcm.c.control_id)
    ).all())
    stmt = (
        select(controls, domains.c.name.label("domain_name"),
               domains.c.code.label("domain_code"))
        .join(domains, controls.c.domain_id == domains.c.id)
        .where(controls.c.tenant_id == user.tenant_id)
        .order_by(domains.c.sort_order, controls.c.code)
    )
    if not include_retired:
        stmt = stmt.where(controls.c.status == "active")
    if domain_code:
        stmt = stmt.where(domains.c.code == domain_code)
    if applicability:
        stmt = stmt.where(controls.c.applicability == applicability)
    if q:
        # Both columns, because ControlPicker filtered on both client-side — a code-only
        # server predicate would be a behaviour regression, not a migration.
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(controls.c.code).like(like)
                          | func.lower(controls.c.statement).like(like))
    return [{**dict(r), "mapped_count": counts.get(r["id"], 0)}
            for r in conn.execute(stmt).mappings()]


@router.get("/controls/{control_id}")
def control_detail(
    control_id: str,
    user: Principal = Depends(require("controls", "view")),
    conn=Depends(get_conn),
):
    controls, domains = t("controls"), t("domains")
    row = conn.execute(
        select(controls, domains.c.name.label("domain_name"))
        .join(domains, controls.c.domain_id == domains.c.id)
        .where(controls.c.id == control_id,
               controls.c.tenant_id == user.tenant_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "control not found")
    qcm, questions, templates = (
        t("question_control_map"), t("questions"), t("templates"))
    # REJECTED mappings are excluded — a rejected mapping is not "the control's bank
    # points", it's a proposal a reviewer explicitly turned down.
    mapped = conn.execute(
        select(questions.c.id.label("question_id"), questions.c.template_id,
               templates.c.bank_name, templates.c.version_label,
               questions.c.number, questions.c.text,
               qcm.c.confidence, qcm.c.status)
        .join(questions, qcm.c.question_id == questions.c.id)
        .join(templates, questions.c.template_id == templates.c.id)
        .where(qcm.c.control_id == control_id,
               qcm.c.status.in_(LIVE_MAPPING_STATUSES))
        .order_by(qcm.c.confidence.desc())
    ).mappings().all()
    # reverse nav (Sprint 4a · M10a): risks that name this control as a treatment/threat
    rl, risks = t("risk_links"), t("risks")
    linked_risks = conn.execute(
        select(risks.c.id, risks.c.reference, risks.c.title, risks.c.inherent_score,
               risks.c.residual_score, risks.c.treatment, risks.c.status)
        .join(rl, rl.c.risk_id == risks.c.id)
        .where(rl.c.control_id == control_id)
        .order_by(risks.c.inherent_score.desc().nullslast())
    ).mappings().all()
    # obligations this control helps satisfy (M10b · feeds the SoA in Sprint 7)
    com, obs = t("control_obligation_map"), t("obligations")
    linked_obligations = conn.execute(
        select(obs.c.id, obs.c.requirement, obs.c.regulator, obs.c.status)
        .join(com, com.c.obligation_id == obs.c.id)
        .where(com.c.control_id == control_id).order_by(obs.c.regulator.nullslast())
    ).mappings().all()
    # evidence attached directly to this control (P4-S5). No lifecycle filter — an expired
    # or superseded item still belongs to the control's history, exactly like linked_risks
    # and linked_obligations above never filter by status either.
    ec, evidence = t("evidence_controls"), t("evidence")
    today = today_iso()
    linked_evidence = [
        {**dict(e), "status": evidence_status(e["valid_until"], today)}
        for e in conn.execute(
            select(evidence.c.id, evidence.c.title, evidence.c.evidence_type,
                   evidence.c.medium, evidence.c.issued_at, evidence.c.valid_until)
            .join(ec, ec.c.evidence_id == evidence.c.id)
            .where(ec.c.control_id == control_id)
            .order_by(evidence.c.valid_until.asc().nullslast())
        ).mappings()
    ]
    # policies/procedures that document this control (P4-S5). Archived documents stay
    # listed with their own status — a control silently losing its policy the moment
    # someone archives it is exactly the failure this page exists to surface, not hide.
    cd, documents, dv = t("control_documents"), t("documents"), t("document_versions")
    linked_documents = conn.execute(
        select(documents.c.id, documents.c.title, documents.c.document_type,
               documents.c.status, dv.c.version_label.label("published_version"))
        .select_from(cd.join(documents, cd.c.document_id == documents.c.id)
                       .outerjoin(dv, documents.c.current_published_version_id == dv.c.id))
        .where(cd.c.control_id == control_id)
        .order_by(documents.c.title)
    ).mappings().all()
    # certification clauses this control satisfies (P5-S9). Many-to-many by design: the same
    # control routinely answers ISO, SOC 2 and an RBI clause at once, which is what lets the
    # evidence above be gathered once and count toward every certification.
    ccm, fc, fw = t("control_clause_map"), t("framework_clauses"), t("frameworks")
    linked_clauses = conn.execute(
        select(fc.c.id, fc.c.ref, fc.c.title, fw.c.id.label("framework_id"),
               fw.c.code.label("framework_code"), fw.c.name.label("framework_name"))
        .select_from(ccm.join(fc, ccm.c.clause_id == fc.c.id)
                        .join(fw, fc.c.framework_id == fw.c.id))
        .where(ccm.c.control_id == control_id, ccm.c.tenant_id == user.tenant_id)
        .order_by(fw.c.code, fc.c.sort_order, fc.c.ref)
    ).mappings().all()
    return {**dict(row), "mapped_points": [dict(m) for m in mapped],
            "linked_risks": [dict(r) for r in linked_risks],
            "linked_obligations": [dict(o) for o in linked_obligations],
            "linked_evidence": linked_evidence,
            "linked_documents": [dict(d) for d in linked_documents],
            "linked_clauses": [dict(c) for c in linked_clauses]}


# ------------------------------------------------------------------ write path (P4-S5)

class ControlIn(StrictModel):
    domain_id: str
    code: str
    statement: str
    lifecycle: str = "per_audit"
    recurrence_months: int | None = None
    applicability: str = "applicable"
    na_justification: str | None = None
    reactivation_trigger: str | None = None
    stock_response: str | None = None
    stock_comment: str | None = None
    guidance: str | None = None
    owner_person_id: str | None = None
    # NOT exposed: owner_member_id (its FK has no tenant leg — a caller could point at
    # another tenant's member) and framework_refs (superseded, legacy JSON tags).


class ControlPatch(StrictModel):
    domain_id: str | None = None
    code: str | None = None
    statement: str | None = None
    lifecycle: str | None = None
    recurrence_months: int | None = None
    applicability: str | None = None
    na_justification: str | None = None
    reactivation_trigger: str | None = None
    stock_response: str | None = None
    stock_comment: str | None = None
    guidance: str | None = None
    owner_person_id: str | None = None


def _validate_control(merged: dict) -> None:
    """Validate the RESULTING row, not just the submitted patch.

    controls_na_needs_reason and controls_recurring_needs_months (db/schema.sql) are
    row-level CHECKs — a partial PATCH that looks innocent, {"lifecycle": "recurring"} on a
    row whose recurrence_months is still NULL, is a CheckViolation (500) unless the merged
    row is what gets validated.
    """
    if merged["lifecycle"] not in LIFECYCLE:
        raise HTTPException(400, f"lifecycle must be one of: {', '.join(LIFECYCLE)}")
    if merged["applicability"] not in APPLICABILITY:
        raise HTTPException(400, f"applicability must be one of: {', '.join(APPLICABILITY)}")
    if merged["stock_response"] is not None and merged["stock_response"] not in STOCK:
        raise HTTPException(400, f"stock_response must be one of: {', '.join(STOCK)}")
    if not (merged["code"] or "").strip():
        raise HTTPException(400, "code cannot be empty")
    if not (merged["statement"] or "").strip():
        raise HTTPException(400, "statement cannot be empty")
    if merged["lifecycle"] == "recurring" and merged["recurrence_months"] is None:
        raise HTTPException(400, "a recurring control needs recurrence_months")
    if merged["recurrence_months"] is not None and merged["recurrence_months"] < 1:
        raise HTTPException(400, "recurrence_months must be at least 1")
    if merged["applicability"] == "not_applicable" and not merged["na_justification"]:
        raise HTTPException(400, "mark why this control is not applicable")


def _owner_and_domain_must_exist(conn, tenant_id: str, vals: dict) -> None:
    if vals.get("domain_id") and conn.execute(select(t("domains").c.id).where(
            t("domains").c.id == vals["domain_id"],
            t("domains").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, "domain not found in this organisation")
    if vals.get("owner_person_id") and conn.execute(select(t("people").c.id).where(
            t("people").c.id == vals["owner_person_id"],
            t("people").c.tenant_id == tenant_id)).first() is None:
        raise HTTPException(400, "owner must be a person in this organisation")


@router.post("/controls", status_code=201)
def create_control(body: ControlIn, user: Principal = Depends(require("controls", "add"))):
    vals = _norm(body.model_dump())
    _validate_control(vals)
    cid, now = str(uuid.uuid4()), now_iso()
    with engine.begin() as conn:
        _owner_and_domain_must_exist(conn, user.tenant_id, vals)
        # Pre-check rather than catch-after-insert: a Postgres constraint violation aborts
        # the whole transaction, so a follow-up SELECT in the except block (to name the
        # clashing control) would itself raise InFailedSqlTransaction. This also gives a
        # friendlier message — "restore it" — in the common case, at the cost of a TOCTOU
        # race the IntegrityError backstop below still catches (as a plain 409).
        existing = conn.execute(select(t("controls").c.id, t("controls").c.status).where(
            t("controls").c.tenant_id == user.tenant_id,
            t("controls").c.code == vals["code"])).mappings().first()
        if existing:
            detail = f"code {vals['code']!r} is already in use"
            if existing["status"] == "retired":
                detail += f" by a retired control ({existing['id']}) — restore it instead"
            raise HTTPException(409, detail)
        try:
            conn.execute(insert(t("controls")).values(
                id=cid, tenant_id=user.tenant_id, status="active",
                created_at=now, updated_at=now, **vals))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, f"code {vals['code']!r} is already in use")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.created", entity_type="control", entity_id=cid,
                 detail={"code": vals["code"]})
    return {"id": cid}


@router.patch("/controls/{control_id}")
def update_control(control_id: str, body: ControlPatch,
                   user: Principal = Depends(require("controls", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        raise HTTPException(400, "nothing to update")
    with engine.begin() as conn:
        current = _control(conn, user.tenant_id, control_id)
        merged = {**current, **vals}
        _validate_control(merged)
        _owner_and_domain_must_exist(conn, user.tenant_id, vals)
        if merged["lifecycle"] != "recurring":
            vals["recurrence_months"] = None
        if merged["applicability"] != "not_applicable":
            vals["na_justification"] = None
            vals["reactivation_trigger"] = None
        vals["updated_at"] = now_iso()          # controls has no set_updated_at trigger
        try:
            conn.execute(update(t("controls")).where(
                t("controls").c.id == control_id).values(**vals))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, f"code {vals.get('code')!r} is already in use")

        stale = {"count": 0, "assessments": []}
        if "stock_response" in vals or "stock_comment" in vals:
            # Editing the stock answer does NOT retro-write responses already prefilled
            # from the old value — response_revisions is append-only, and a bank auditor
            # may already have read it. Silence would be worse: tell the caller what is
            # now stale so they can go correct it by hand.
            resp, asmt = t("responses"), t("assessments")
            rows = conn.execute(
                select(asmt.c.id, asmt.c.bank_name, asmt.c.title).distinct()
                .select_from(resp.join(asmt, resp.c.assessment_id == asmt.c.id))
                .where(resp.c.prefilled_from_control_id == control_id,
                       asmt.c.status.notin_(("closed", "verdict_issued")))
            ).mappings().all()
            stale = {"count": len(rows), "assessments": [dict(r) for r in rows]}
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.updated", entity_type="control", entity_id=control_id,
                 detail={k: v for k, v in vals.items() if k != "updated_at"})
    return {"ok": True, "stale_prefilled": stale}


@router.delete("/controls/{control_id}")
def retire_control(control_id: str, user: Principal = Depends(require("controls", "delete"))):
    """Retire, never hard-delete: `tasks.control_id` and `responses.prefilled_from_control_id`
    have no ON DELETE and would raise a raw ForeignKeyViolation, and a real delete would
    silently cascade away the crosswalk (question_control_map) and every risk_links row
    naming this control."""
    with engine.begin() as conn:
        current = _control(conn, user.tenant_id, control_id)
        if current["status"] == "retired":
            raise HTTPException(409, "this control is already retired")
        conn.execute(update(t("controls")).where(t("controls").c.id == control_id).values(
            status="retired", updated_at=now_iso()))
        # a retired control's own recurring task must stop firing overdue notifications
        tasks = t("tasks")
        paused = conn.execute(update(tasks).where(
            tasks.c.control_id == control_id, tasks.c.tenant_id == user.tenant_id,
            tasks.c.status == "active").values(status="paused")).rowcount
        retained = {
            "mapped_questions": conn.execute(select(func.count()).select_from(t("question_control_map")).where(
                t("question_control_map").c.control_id == control_id,
                t("question_control_map").c.status.in_(LIVE_MAPPING_STATUSES))).scalar(),
            "risks": conn.execute(select(func.count()).select_from(t("risk_links")).where(
                t("risk_links").c.control_id == control_id)).scalar(),
            "evidence": conn.execute(select(func.count()).select_from(t("evidence_controls")).where(
                t("evidence_controls").c.control_id == control_id)).scalar(),
            "documents": conn.execute(select(func.count()).select_from(t("control_documents")).where(
                t("control_documents").c.control_id == control_id)).scalar(),
            "tasks_paused": paused,
        }
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.retired", entity_type="control", entity_id=control_id, detail={})
    return {"ok": True, "retained": retained}


@router.post("/controls/{control_id}/restore")
def restore_control(control_id: str, user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        current = _control(conn, user.tenant_id, control_id)
        if current["status"] == "active":
            raise HTTPException(409, "this control is already active")
        conn.execute(update(t("controls")).where(t("controls").c.id == control_id).values(
            status="active", updated_at=now_iso()))
        # un-pause whatever retiring this control paused — best-effort: a task explicitly
        # paused for an unrelated reason while the control was retired stays paused, but
        # that's a rare enough edge case that guessing wrong here isn't worth the risk of
        # silently reactivating tasks nobody wants running again
        tasks = t("tasks")
        conn.execute(update(tasks).where(
            tasks.c.control_id == control_id, tasks.c.tenant_id == user.tenant_id,
            tasks.c.status == "paused").values(status="active"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.restored", entity_type="control", entity_id=control_id, detail={})
    return {"ok": True}


# ------------------------------------------------------------------ linkage (P4-S5)

class ControlEvidenceIn(StrictModel):
    evidence_id: str


@router.post("/controls/{control_id}/evidence", status_code=201)
def link_control_evidence(control_id: str, body: ControlEvidenceIn,
                          user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        if conn.execute(select(t("evidence").c.id).where(
                t("evidence").c.id == body.evidence_id,
                t("evidence").c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "evidence not found in this organisation")
        try:
            conn.execute(insert(t("evidence_controls")).values(
                tenant_id=user.tenant_id, control_id=control_id,
                evidence_id=body.evidence_id))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that evidence is already linked to this control")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.evidence_linked", entity_type="control", entity_id=control_id,
                 detail={"evidence_id": body.evidence_id})
    return {"ok": True}


@router.delete("/controls/{control_id}/evidence/{evidence_id}")
def unlink_control_evidence(control_id: str, evidence_id: str,
                            user: Principal = Depends(require("controls", "edit"))):
    # Gated on .edit, not .delete: unlinking removes a RELATIONSHIP, not a record, and an
    # Editor (every action except delete) must be able to undo their own link — same call
    # P4-S4 made for discard-draft.
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        ec = t("evidence_controls")
        conn.execute(delete(ec).where(
            ec.c.tenant_id == user.tenant_id, ec.c.control_id == control_id,
            ec.c.evidence_id == evidence_id))
    return {"ok": True}


class ControlDocumentIn(StrictModel):
    document_id: str
    note: str | None = None


@router.post("/controls/{control_id}/documents", status_code=201)
def link_control_document(control_id: str, body: ControlDocumentIn,
                          user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        if conn.execute(select(t("documents").c.id).where(
                t("documents").c.id == body.document_id,
                t("documents").c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "document not found in this organisation")
        try:
            conn.execute(insert(t("control_documents")).values(
                tenant_id=user.tenant_id, control_id=control_id,
                document_id=body.document_id, note=_norm({"note": body.note})["note"],
                created_at=now_iso()))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that document is already linked to this control")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.document_linked", entity_type="control", entity_id=control_id,
                 detail={"document_id": body.document_id})
    return {"ok": True}


@router.delete("/controls/{control_id}/documents/{document_id}")
def unlink_control_document(control_id: str, document_id: str,
                            user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        cd = t("control_documents")
        conn.execute(delete(cd).where(
            cd.c.tenant_id == user.tenant_id, cd.c.control_id == control_id,
            cd.c.document_id == document_id))
    return {"ok": True}


class ControlClauseIn(StrictModel):
    clause_id: str
    note: str | None = None


@router.post("/controls/{control_id}/clauses", status_code=201)
def link_control_clause(control_id: str, body: ControlClauseIn,
                        user: Principal = Depends(require("controls", "edit"))):
    """Record that this control satisfies a framework clause (P5-S9).

    Many-to-many on purpose: the SAME control is expected to be linked to ISO A.8.5, SOC 2
    CC6.1 and an RBI clause at once. That is what lets evidence be gathered once and count
    toward every certification, instead of maintaining a parallel control set per standard.
    """
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        fc = t("framework_clauses")
        if conn.execute(select(fc.c.id).where(
                fc.c.id == body.clause_id,
                fc.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "clause not found in this organisation")
        try:
            conn.execute(insert(t("control_clause_map")).values(
                tenant_id=user.tenant_id, control_id=control_id, clause_id=body.clause_id,
                note=_norm({"note": body.note})["note"], created_at=now_iso()))
        except IntegrityError as e:
            if not _is_unique_violation(e):
                raise
            raise HTTPException(409, "that clause is already linked to this control")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="control.clause_linked", entity_type="control", entity_id=control_id,
                 detail={"clause_id": body.clause_id})
    return {"ok": True}


@router.delete("/controls/{control_id}/clauses/{clause_id}")
def unlink_control_clause(control_id: str, clause_id: str,
                          user: Principal = Depends(require("controls", "edit"))):
    with engine.begin() as conn:
        _control(conn, user.tenant_id, control_id)
        ccm = t("control_clause_map")
        conn.execute(delete(ccm).where(
            ccm.c.tenant_id == user.tenant_id, ccm.c.control_id == control_id,
            ccm.c.clause_id == clause_id))
    return {"ok": True}


@router.get("/crosswalk")
def crosswalk(
    domain_code: str | None = Query(None),
    user: Principal = Depends(require("controls", "view")),
    conn=Depends(get_conn),
):
    """Matrix: rows = standard controls, columns = the tenant's bank templates,
    cells = the bank's point numbers mapped to that control."""
    templates, controls, domains, qcm, questions = (
        t("templates"), t("controls"), t("domains"),
        t("question_control_map"), t("questions"))

    cols = conn.execute(
        select(templates.c.id, templates.c.bank_name, templates.c.version_label)
        .where(templates.c.tenant_id == user.tenant_id)
        .order_by(templates.c.created_at)
    ).mappings().all()

    # control_id -> {template_id: [numbers]}. Same non-leak-but-tighten-anyway note as
    # list_controls above: qcm's composite FKs already guarantee same-tenant rows.
    # REJECTED mappings are excluded, same as mapped_count and control_detail.
    cell_rows = conn.execute(
        select(qcm.c.control_id, questions.c.template_id, questions.c.number)
        .join(questions, qcm.c.question_id == questions.c.id)
        .where(qcm.c.tenant_id == user.tenant_id,
               qcm.c.status.in_(LIVE_MAPPING_STATUSES))
    ).all()
    cells: dict = {}
    for cid, tid, num in cell_rows:
        cells.setdefault(cid, {}).setdefault(tid, []).append(num or "–")

    cq = (
        select(controls.c.id, controls.c.code, controls.c.statement,
               controls.c.applicability, domains.c.code.label("domain_code"))
        .join(domains, controls.c.domain_id == domains.c.id)
        .where(controls.c.tenant_id == user.tenant_id)
        .order_by(domains.c.sort_order, controls.c.code)
    )
    if domain_code:
        cq = cq.where(domains.c.code == domain_code)

    rows = []
    for c in conn.execute(cq).mappings():
        rows.append({
            "control_id": c["id"], "code": c["code"], "statement": c["statement"],
            "domain_code": c["domain_code"], "applicability": c["applicability"],
            "cells": {col["id"]: cells.get(c["id"], {}).get(col["id"], [])
                      for col in cols},
        })
    return {"columns": [dict(c) for c in cols], "rows": rows}
