"""Controls menu — the standard control framework + the bank crosswalk.

All endpoints are tenant-scoped to the caller's membership (M1 auth). The
data is produced by scripts/build_control_library.py (M2).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from api.auth import Principal, get_current_user
from api.database import get_conn, t

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/domains")
def list_domains(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
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
    user: Principal = Depends(get_current_user),
    conn=Depends(get_conn),
):
    controls, domains, qcm = t("controls"), t("domains"), t("question_control_map")
    # mapped-question count per control
    counts = dict(conn.execute(
        select(qcm.c.control_id, func.count()).group_by(qcm.c.control_id)
    ).all())
    q = (
        select(controls, domains.c.name.label("domain_name"),
               domains.c.code.label("domain_code"))
        .join(domains, controls.c.domain_id == domains.c.id)
        .where(controls.c.tenant_id == user.tenant_id)
        .order_by(domains.c.sort_order, controls.c.code)
    )
    if domain_code:
        q = q.where(domains.c.code == domain_code)
    if applicability:
        q = q.where(controls.c.applicability == applicability)
    return [{**dict(r), "mapped_count": counts.get(r["id"], 0)}
            for r in conn.execute(q).mappings()]


@router.get("/controls/{control_id}")
def control_detail(
    control_id: str,
    user: Principal = Depends(get_current_user),
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
    mapped = conn.execute(
        select(templates.c.bank_name, templates.c.version_label,
               questions.c.number, questions.c.text,
               qcm.c.confidence, qcm.c.status)
        .join(questions, qcm.c.question_id == questions.c.id)
        .join(templates, questions.c.template_id == templates.c.id)
        .where(qcm.c.control_id == control_id)
        .order_by(qcm.c.confidence.desc())
    ).mappings().all()
    return {**dict(row), "mapped_points": [dict(m) for m in mapped]}


@router.get("/crosswalk")
def crosswalk(
    domain_code: str | None = Query(None),
    user: Principal = Depends(get_current_user),
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

    # control_id -> {template_id: [numbers]}
    cell_rows = conn.execute(
        select(qcm.c.control_id, questions.c.template_id, questions.c.number)
        .join(questions, qcm.c.question_id == questions.c.id)
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
