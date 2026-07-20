"""Recurring-task engine (M5).

Pure functions over a connection so they're deterministic and testable; the
APScheduler job (main.py) just calls run_maintenance() on an interval.

Model: each recurring control (lifecycle='recurring' + recurrence_months)
yields one `task`; a task has a stream of `task_runs` (occurrences). Completing
a run rolls the task forward and opens the next run — and can attach the dated
evidence artifact the run produced (the "answer the recurring question with a
fresh dated artifact" loop).
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select, update

from api.database import engine, t
from api.util import add_months, now_iso, today_iso


def _uid() -> str:
    return str(uuid.uuid4())


def generate_tasks(conn, tenant_id: str, today: str | None = None) -> int:
    """Create a task (+ first pending run) for each recurring control that
    doesn't have one yet. Idempotent."""
    today = today or today_iso()
    controls, tasks, runs = t("controls"), t("tasks"), t("task_runs")
    have = set(conn.execute(
        select(tasks.c.control_id).where(tasks.c.tenant_id == tenant_id,
                                         tasks.c.control_id.isnot(None))).scalars())
    rows = conn.execute(
        select(controls.c.id, controls.c.statement, controls.c.recurrence_months)
        .where(controls.c.tenant_id == tenant_id, controls.c.status == "active",
               controls.c.lifecycle == "recurring",
               controls.c.recurrence_months.isnot(None))
    ).mappings().all()
    created = 0
    for c in rows:
        if c["id"] in have:
            continue
        task_id = _uid()
        next_due = add_months(today, c["recurrence_months"])
        conn.execute(insert(tasks).values(
            id=task_id, tenant_id=tenant_id, control_id=c["id"],
            title=f"Recurring: {c['statement']}", cadence_months=c["recurrence_months"],
            next_due_at=next_due, status="active", created_at=now_iso()))
        conn.execute(insert(runs).values(
            id=_uid(), task_id=task_id, due_at=next_due, status="pending"))
        created += 1
    return created


def mark_overdue(conn, today: str | None = None) -> int:
    """Flip pending runs whose due date has passed to 'overdue', and notify the
    task assignee. Returns the number flipped."""
    today = today or today_iso()
    runs, tasks = t("task_runs"), t("tasks")
    due = conn.execute(
        select(runs.c.id, runs.c.task_id, tasks.c.tenant_id, tasks.c.title,
               tasks.c.assignee_member_id)
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(runs.c.status == "pending", runs.c.due_at < today)
    ).mappings().all()
    members = t("tenant_members")
    for r in due:
        conn.execute(update(runs).where(runs.c.id == r["id"]).values(status="overdue"))
        if r["assignee_member_id"]:
            uid_ = conn.execute(select(members.c.user_id).where(
                members.c.id == r["assignee_member_id"])).scalar()
            if uid_:
                conn.execute(insert(t("notifications")).values(
                    id=_uid(), tenant_id=r["tenant_id"], user_id=uid_,
                    type="task_overdue", title=f"Overdue: {r['title']}",
                    entity_type="task", entity_id=r["task_id"], created_at=now_iso()))
    return len(due)


def complete_run(conn, tenant_id: str, run_id: str, member_id: str | None,
                 evidence_id: str | None, notes: str | None,
                 today: str | None = None) -> dict:
    """Mark a run done (optionally attaching the evidence it produced), roll the
    task forward, and open the next pending run."""
    today = today or today_iso()
    runs, tasks = t("task_runs"), t("tasks")
    run = conn.execute(
        select(runs.c.id, runs.c.task_id, tasks.c.tenant_id, tasks.c.cadence_months)
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(runs.c.id == run_id, tasks.c.tenant_id == tenant_id)
    ).mappings().first()
    if run is None:
        return {}
    conn.execute(update(runs).where(runs.c.id == run_id).values(
        status="done", completed_at=now_iso(), completed_by_member_id=member_id,
        evidence_id=evidence_id, notes=notes))
    next_due = None
    if run["cadence_months"]:
        next_due = add_months(today, run["cadence_months"])
        conn.execute(update(tasks).where(tasks.c.id == run["task_id"]).values(
            next_due_at=next_due))
        conn.execute(insert(runs).values(
            id=_uid(), task_id=run["task_id"], due_at=next_due, status="pending"))
    return {"task_id": run["task_id"], "next_due_at": next_due}


def deactivate_expired_people(conn, today: str | None = None) -> int:
    """Flip people whose contract has ended to INACTIVE (Sprint 1 / M8).

    Answers KSL #15 ("process for revoking access when employees leave") by making
    the register self-maintaining: nobody has to remember to tick the box. The
    v_people_effective_state view derives the same thing for reads; this persists
    it so `state` and reality don't drift.
    """
    today = today or today_iso()
    people = t("people")
    res = conn.execute(update(people).where(
        people.c.state == "ACTIVE",
        people.c.contract_end_date.isnot(None),
        people.c.contract_end_date < today,
    ).values(state="INACTIVE", updated_at=now_iso()))
    return res.rowcount or 0


def run_maintenance() -> None:
    """Scheduler entry point: generate tasks for every tenant, flip overdue,
    and retire people whose contracts have ended."""
    with engine.begin() as conn:
        tenant_ids = list(conn.execute(select(t("tenants").c.id)).scalars())
        for tid in tenant_ids:
            generate_tasks(conn, tid)
        mark_overdue(conn)
        deactivate_expired_people(conn)
