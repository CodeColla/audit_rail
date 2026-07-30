"""Dashboard — audit-readiness summary and the four 'needs attention' queues (M5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.auth import Principal, get_current_user
from api.permissions import require
from api.database import get_conn, t
from api.util import evidence_status, review_status, today_iso

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: Principal = Depends(require("dashboard", "view")), conn=Depends(get_conn)):
    tid = user.tenant_id
    today = today_iso()
    a, q, r = t("assessments"), t("questions"), t("responses")

    # readiness per assessment
    assessments = conn.execute(select(a).where(a.c.tenant_id == tid)).mappings().all()
    total_q = total_a = stock_a = 0
    readiness = []
    for asm in assessments:
        tq = conn.execute(select(func.count()).where(
            q.c.template_id == asm["template_id"])).scalar()
        an = conn.execute(select(func.count()).where(
            r.c.assessment_id == asm["id"], r.c.response_value.isnot(None))).scalar()
        st = conn.execute(select(func.count()).where(
            r.c.assessment_id == asm["id"],
            r.c.prefilled_from_control_id.isnot(None))).scalar()
        total_q += tq; total_a += an; stock_a += st
        readiness.append({
            "assessment_id": asm["id"], "bank_name": asm["bank_name"],
            "status": asm["status"], "answered": an, "total": tq,
            "pct": round(100 * an / tq) if tq else 0})

    open_asks_n = conn.execute(
        select(func.count()).select_from(r).join(a, r.c.assessment_id == a.c.id)
        .where(a.c.tenant_id == tid, r.c.workflow_status == "ask_pending")).scalar()

    # evidence freshness
    ev = t("evidence")
    # Only FULFILLED evidence can be "fresh" — counting draft/requested placeholders in the
    # denominator quietly deflated the number.
    ev_rows = conn.execute(select(ev.c.valid_until).where(
        ev.c.tenant_id == tid, ev.c.state == "FULFILLED")).scalars().all()
    fresh = sum(1 for v in ev_rows if evidence_status(v, today) in ("valid", "no_expiry"))
    ev_total = len(ev_rows)

    kpis = {
        "overall_readiness_pct": round(100 * total_a / total_q) if total_q else 0,
        "answered_from_stock_pct": round(100 * stock_a / total_a) if total_a else 0,
        "open_auditor_asks": open_asks_n,
        "evidence_freshness_pct": round(100 * fresh / ev_total) if ev_total else 100,
    }

    # ── queues ──────────────────────────────────────────────────────────────
    tasks, runs = t("tasks"), t("task_runs")
    overdue_tasks = [dict(x) for x in conn.execute(
        select(runs.c.id.label("run_id"), runs.c.due_at, runs.c.status,
               tasks.c.id.label("task_id"), tasks.c.title)
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(tasks.c.tenant_id == tid, runs.c.status.in_(("pending", "overdue")),
               runs.c.due_at < today)
        .order_by(runs.c.due_at)).mappings()]

    expiring_evidence = []
    for e in conn.execute(select(ev).where(ev.c.tenant_id == tid)).mappings():
        s = evidence_status(e["valid_until"], today)
        if s in ("expired", "expiring"):
            expiring_evidence.append({"id": e["id"], "title": e["title"],
                                      "valid_until": e["valid_until"], "status": s})

    # vendor security assessments expire too (D-MOAT extended to third parties, M10b) —
    # a lapsed vendor review is not current assurance, so it joins the same queue.
    asm, tp = t("third_party_assessments"), t("third_parties")
    expiring_assessments = []
    for row in conn.execute(
            select(asm.c.id, asm.c.expires_at, asm.c.outcome, tp.c.name.label("third_party_name"))
            .join(tp, asm.c.third_party_id == tp.c.id)
            .where(asm.c.tenant_id == tid)).mappings():
        s = evidence_status(row["expires_at"], today)
        if s in ("expired", "expiring"):
            expiring_assessments.append({"id": row["id"], "third_party_name": row["third_party_name"],
                                         "expires_at": row["expires_at"], "outcome": row["outcome"],
                                         "status": s})

    # Documents due for review. Post-pivot these live in `documents`; the legacy `policies`
    # table is transitional (schema.sql: "drop at end of M9"). Read BOTH, but skip any legacy
    # policy that has already been folded into a document (documents.legacy_policy_id) so a
    # migrated policy isn't counted twice.
    doc, pol = t("documents"), t("policies")
    documents_due = []
    for d in conn.execute(select(doc).where(doc.c.tenant_id == tid,
                                            doc.c.status == "ACTIVE")).mappings():
        s = review_status(d["next_review_at"], today)
        if s in ("overdue", "due_soon"):
            documents_due.append({"id": d["id"], "title": d["title"], "kind": "document",
                                  "next_review_at": d["next_review_at"], "review_status": s})
    migrated = set(conn.execute(select(doc.c.legacy_policy_id).where(
        doc.c.tenant_id == tid, doc.c.legacy_policy_id.isnot(None))).scalars())
    for p in conn.execute(select(pol).where(pol.c.tenant_id == tid,
                                            pol.c.status == "active")).mappings():
        if p["id"] in migrated:
            continue
        s = review_status(p["next_review_at"], today)
        if s in ("overdue", "due_soon"):
            documents_due.append({"id": p["id"], "title": p["title"], "kind": "policy",
                                  "next_review_at": p["next_review_at"], "review_status": s})

    open_asks = [dict(x) for x in conn.execute(
        select(r.c.id, r.c.assessment_id, q.c.number, q.c.text, a.c.bank_name)
        .select_from(r).join(a, r.c.assessment_id == a.c.id)
        .join(q, r.c.question_id == q.c.id)
        .where(a.c.tenant_id == tid, r.c.workflow_status == "ask_pending")
        .limit(20)).mappings()]

    return {
        "kpis": kpis,
        "readiness_by_bank": readiness,
        "queues": {
            "overdue_tasks": overdue_tasks,
            "expiring_evidence": sorted(expiring_evidence,
                                        key=lambda x: x["valid_until"] or "9999"),
            "expiring_assessments": sorted(expiring_assessments,
                                           key=lambda x: x["expires_at"] or "9999"),
            "documents_due": sorted(documents_due,
                                    key=lambda x: x["next_review_at"] or "9999"),
            "open_auditor_asks": open_asks,
        },
    }
