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

from api.core.database import engine, t
from api.core.util import add_days, add_months, now_iso, today_iso


def _uid() -> str:
    return str(uuid.uuid4())


#: P4-S9. Two recurrence systems coexist on `tasks`, never on the same row (the DB CHECK
#: `tasks_recurrence_not_both` enforces that): `cadence_months`, written only by
#: `generate_tasks()` below for recurring controls, and `frequency`/`interval_count`,
#: written only by the tasks API for hand-created tasks. QUARTERLY/YEARLY are sugar over
#: `add_months` with a baked-in multiplier — QUARTERLY is mechanically MONTHLY x3, kept
#: as its own value because that's the word a compliance calendar actually uses.
def next_occurrence(anchor: str, *, frequency: str | None, interval_count: int | None,
                    cadence_months: int | None) -> str | None:
    """The next due date after `anchor`, or None if the task does not recur."""
    if frequency == "DAILY":
        return add_days(anchor, interval_count)
    if frequency == "WEEKLY":
        return add_days(anchor, interval_count * 7)
    if frequency == "MONTHLY":
        return add_months(anchor, interval_count)
    if frequency == "QUARTERLY":
        return add_months(anchor, interval_count * 3)
    if frequency == "YEARLY":
        return add_months(anchor, interval_count * 12)
    if cadence_months:
        return add_months(anchor, cadence_months)
    return None


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
    task assignee. Returns the number flipped.

    Skips paused tasks. `library.py`'s control-retire already flips a task to 'paused'
    on the belief that a paused task stops nagging — before P4-S9 that was only cosmetic:
    this query ignored task status entirely, so a paused task's stale run still flipped to
    overdue and still fired a notification every maintenance pass.
    """
    today = today or today_iso()
    runs, tasks = t("task_runs"), t("tasks")
    due = conn.execute(
        select(runs.c.id, runs.c.task_id, tasks.c.tenant_id, tasks.c.title,
               tasks.c.assignee_member_id)
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(runs.c.status == "pending", runs.c.due_at < today,
               tasks.c.status == "active")
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


def complete_run(conn, tenant_id: str, task_id: str, run_id: str, member_id: str | None,
                 evidence_id: str | None, notes: str | None) -> dict:
    """Mark a run done (optionally attaching the evidence it produced), roll the
    task forward, and open the next pending run — or, for a one-off task, close it out.

    The rollover anchors on the RUN's own `due_at`, not on `today`. P4-S9 changed this
    deliberately: anchoring on today meant finishing a quarterly review two weeks early
    pushed the whole schedule two weeks late, forever — the schedule drifted every time
    someone got ahead of it. Anchoring on due_at means the schedule is the schedule; a run
    completed very late can legitimately open its next occurrence already overdue, which is
    correct (the review that was actually due in between was, in fact, missed) rather than
    silently absorbed into "on time because you got to it eventually".

    `task_id` is now matched against the run's actual task. `run_id` alone already
    determined which task got rolled forward, so that was never wrong — but the path's
    `task_id` was otherwise decorative, so `/tasks/<any-id>/runs/<real-run-id>/complete`
    would complete the run and log the activity entry under whatever task_id the caller
    typed, not the run's real one. A mismatch is now a clean 404 instead of a silently
    mislabelled activity-log entry.
    """
    runs, tasks = t("task_runs"), t("tasks")
    run = conn.execute(
        select(runs.c.id, runs.c.task_id, runs.c.due_at, tasks.c.cadence_months,
               tasks.c.frequency, tasks.c.interval_count)
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(runs.c.id == run_id, runs.c.task_id == task_id, tasks.c.tenant_id == tenant_id)
    ).mappings().first()
    if run is None:
        return {}
    conn.execute(update(runs).where(runs.c.id == run_id).values(
        status="done", completed_at=now_iso(), completed_by_member_id=member_id,
        evidence_id=evidence_id, notes=notes))
    next_due = next_occurrence(run["due_at"], frequency=run["frequency"],
                               interval_count=run["interval_count"],
                               cadence_months=run["cadence_months"])
    if next_due:
        conn.execute(update(tasks).where(tasks.c.id == run["task_id"]).values(
            next_due_at=next_due))
        conn.execute(insert(runs).values(
            id=_uid(), task_id=run["task_id"], due_at=next_due, status="pending"))
    else:
        # a one-off task has nothing left to schedule — it's finished, not just "the one
        # run happened to be its last": list_tasks defaults to status='active', so leaving
        # this row active would make it sit in the list forever with no run to act on
        conn.execute(update(tasks).where(tasks.c.id == run["task_id"]).values(
            status="completed"))
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
