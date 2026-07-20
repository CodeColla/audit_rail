"""Dashboard — audit-readiness summary and the four 'needs attention' queues (M5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.auth import Principal, get_current_user
from api.database import get_conn, t
from api.util import evidence_status, review_status, today_iso

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
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
    ev_rows = conn.execute(select(ev.c.valid_until).where(ev.c.tenant_id == tid)).scalars().all()
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

    pol = t("policies")
    policies_due = []
    for p in conn.execute(select(pol).where(pol.c.tenant_id == tid,
                                            pol.c.status == "active")).mappings():
        s = review_status(p["next_review_at"], today)
        if s in ("overdue", "due_soon"):
            policies_due.append({"id": p["id"], "title": p["title"],
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
            "policies_due": sorted(policies_due,
                                   key=lambda x: x["next_review_at"] or "9999"),
            "open_auditor_asks": open_asks,
        },
    }
