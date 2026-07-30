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
    q = (
        select(controls, domains.c.name.label("domain_name"),
               domains.c.code.label("domain_code"))
        .join(domains, controls.c.domain_id == domains.c.id)
        .where(controls.c.tenant_id == user.tenant_id)
        .order_by(domains.c.sort_order, controls.c.code)
    )
    if not include_retired:
        q = q.where(controls.c.status == "active")
    if domain_code:
        q = q.where(domains.c.code == domain_code)
    if applicability:
        q = q.where(controls.c.applicability == applicability)
    return [{**dict(r), "mapped_count": counts.get(r["id"], 0)}
            for r in conn.execute(q).mappings()]


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
    return {**dict(row), "mapped_points": [dict(m) for m in mapped],
            "linked_risks": [dict(r) for r in linked_risks],
            "linked_obligations": [dict(o) for o in linked_obligations],
            "linked_evidence": linked_evidence,
            "linked_documents": [dict(d) for d in linked_documents]}


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
    cell_rows = conn.execute(
        select(qcm.c.control_id, questions.c.template_id, questions.c.number)
        .join(questions, qcm.c.question_id == questions.c.id)
        .where(qcm.c.tenant_id == user.tenant_id)
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
