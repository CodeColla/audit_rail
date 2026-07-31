"""Tasks & compliance calendar (M5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sqldelete, insert, select, update

from api import activity, tasks_engine
from api.auth import Principal, get_current_user
from api.permissions import require
from api.database import engine, get_conn, t
from api.util import IsoDate, StrictModel, now_iso, today_iso

FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY")

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _member_id(conn, tenant_id, user_id):
    m = t("tenant_members")
    return conn.execute(select(m.c.id).where(
        m.c.tenant_id == tenant_id, m.c.user_id == user_id)).scalar()


def _next_run(conn, task_id):
    runs = t("task_runs")
    return conn.execute(
        select(runs).where(runs.c.task_id == task_id,
                           runs.c.status.in_(("pending", "overdue")))
        .order_by(runs.c.due_at).limit(1)).mappings().first()


def _norm(vals: dict) -> dict:
    """Empty/whitespace strings -> None, so a form clearing a date sends "" and the column
    ends up NULL rather than tripping an iso_ts CHECK. Copied, not imported, per this
    codebase's convention that routers don't reach into each other's private helpers."""
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in vals.items()}


def _reject_null_required(vals: dict, required: tuple[str, ...]):
    """A blanked-out required field must be a clean 400, not a NOT NULL IntegrityError."""
    for k in required:
        if k in vals and vals[k] is None:
            raise HTTPException(400, f"{k.replace('_', ' ')} cannot be empty")


def _validate_recurrence(vals: dict):
    """`tasks_recurrence_shape` and `tasks_recurrence_not_both` are DB CHECKs — this is the
    same rule, checked first, so a bad combination is a clean 400 instead of an
    IntegrityError. `cadence_months` is legacy (generate_tasks() writes it for
    recurring-control tasks); this endpoint only ever writes frequency/interval_count."""
    freq, interval = vals.get("frequency"), vals.get("interval_count")
    if (freq is None) != (interval is None):
        raise HTTPException(400, "frequency and interval_count must be set together")
    if freq is not None and freq not in FREQUENCIES:
        raise HTTPException(400, f"frequency must be one of {', '.join(FREQUENCIES)}")
    if interval is not None and interval <= 0:
        raise HTTPException(400, "interval_count must be a positive number")
    if vals.get("cadence_months") is not None and freq is not None:
        raise HTTPException(400, "set either a recurrence frequency or cadence_months, not both")


def _tenant_row_exists(conn, table: str, tenant_id: str, row_id: str | None) -> bool:
    if row_id is None:
        return True
    tbl = t(table)
    return conn.execute(select(tbl.c.id).where(
        tbl.c.id == row_id, tbl.c.tenant_id == tenant_id)).first() is not None


def _validate_links(conn, tenant_id: str, vals: dict):
    for field, table, label in (
        ("control_id", "controls", "control"), ("policy_id", "policies", "policy"),
        ("document_id", "documents", "document"), ("risk_id", "risks", "risk"),
        ("assessment_id", "assessments", "assessment"),
    ):
        if field in vals and not _tenant_row_exists(conn, table, tenant_id, vals[field]):
            raise HTTPException(400, f"{label} not found in this organisation")
    if "assignee_person_id" in vals and not _tenant_row_exists(
            conn, "people", tenant_id, vals["assignee_person_id"]):
        raise HTTPException(400, "person not found in this organisation")
    if "assignee_member_id" in vals and not _tenant_row_exists(
            conn, "tenant_members", tenant_id, vals["assignee_member_id"]):
        raise HTTPException(400, "member not found in this organisation")


@router.post("/generate")
def generate(user: Principal = Depends(require("tasks", "edit"))):
    with engine.begin() as conn:
        created = tasks_engine.generate_tasks(conn, user.tenant_id)
        flipped = tasks_engine.mark_overdue(conn)
        retired = tasks_engine.deactivate_expired_people(conn)
    return {"tasks_created": created, "runs_marked_overdue": flipped,
            "people_deactivated": retired}


class TaskIn(StrictModel):
    title: str
    description: str | None = None
    cadence_months: int | None = None            # legacy monthly-only; new tasks use frequency
    frequency: str | None = None                  # DAILY | WEEKLY | MONTHLY | QUARTERLY | YEARLY
    interval_count: int | None = None
    assignee_member_id: str | None = None
    assignee_person_id: str | None = None
    next_due_at: IsoDate = None
    control_id: str | None = None
    policy_id: str | None = None
    document_id: str | None = None
    risk_id: str | None = None
    assessment_id: str | None = None


@router.post("", status_code=201)
def create_task(body: TaskIn, user: Principal = Depends(require("tasks", "add"))):
    vals = _norm(body.model_dump())
    _reject_null_required(vals, ("title",))
    _validate_recurrence(vals)
    tid = str(uuid.uuid4())
    with engine.begin() as conn:
        _validate_links(conn, user.tenant_id, vals)
        conn.execute(insert(t("tasks")).values(
            id=tid, tenant_id=user.tenant_id, status="active", created_at=now_iso(), **vals))
        if vals.get("next_due_at"):
            conn.execute(insert(t("task_runs")).values(
                id=str(uuid.uuid4()), task_id=tid, due_at=vals["next_due_at"],
                status="pending"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.created", entity_type="task", entity_id=tid)
    return {"id": tid}


@router.get("")
def list_tasks(mine: bool = Query(False),
               status: str = Query("active", pattern="^(active|paused|completed|all)$"),
               user: Principal = Depends(require("tasks", "view")),
               conn=Depends(get_conn)):
    tasks = t("tasks")
    q = select(tasks).where(tasks.c.tenant_id == user.tenant_id).order_by(tasks.c.next_due_at)
    if status != "all":
        q = q.where(tasks.c.status == status)
    if mine:
        q = q.where(tasks.c.assignee_member_id == _member_id(
            conn, user.tenant_id, user.user_id))
    out = []
    for task in conn.execute(q).mappings():
        nr = _next_run(conn, task["id"])
        out.append({**dict(task),
                    "next_run": dict(nr) if nr else None,
                    "run_status": nr["status"] if nr else "none"})
    return out


@router.get("/calendar")
def calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
             user: Principal = Depends(require("tasks", "view")), conn=Depends(get_conn)):
    """Task runs due within the given YYYY-MM (for the month grid)."""
    tasks, runs = t("tasks"), t("task_runs")
    rows = conn.execute(
        select(runs.c.id, runs.c.due_at, runs.c.status, tasks.c.title, tasks.c.id.label("task_id"))
        .join(tasks, runs.c.task_id == tasks.c.id)
        .where(tasks.c.tenant_id == user.tenant_id,
               runs.c.due_at.like(f"{month}-%"))
        .order_by(runs.c.due_at)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{task_id}")
def task_detail(task_id: str, user: Principal = Depends(require("tasks", "view")),
                conn=Depends(get_conn)):
    tasks, runs = t("tasks"), t("task_runs")
    task = conn.execute(select(tasks).where(
        tasks.c.id == task_id, tasks.c.tenant_id == user.tenant_id)).mappings().first()
    if task is None:
        raise HTTPException(404, "task not found")
    run_rows = conn.execute(select(runs).where(runs.c.task_id == task_id)
                            .order_by(runs.c.due_at.desc())).mappings().all()
    # Same shape as list_tasks: `next_run`/`run_status` are what the UI's Complete action
    # keys off, and were missing here entirely — a task detail fetched straight from
    # /tasks/{id} (as the drawer does) could never show a Complete button, even with a
    # real pending run sitting in `runs`.
    nr = _next_run(conn, task_id)
    return {**dict(task), "runs": [dict(r) for r in run_rows],
            "next_run": dict(nr) if nr else None,
            "run_status": nr["status"] if nr else "none"}


class TaskPatch(StrictModel):
    title: str | None = None
    description: str | None = None
    cadence_months: int | None = None
    frequency: str | None = None
    interval_count: int | None = None
    assignee_member_id: str | None = None
    assignee_person_id: str | None = None
    next_due_at: IsoDate = None
    control_id: str | None = None
    policy_id: str | None = None
    document_id: str | None = None
    risk_id: str | None = None
    assessment_id: str | None = None


def _get_task(conn, tenant_id: str, task_id: str) -> dict:
    row = conn.execute(select(t("tasks")).where(
        t("tasks").c.id == task_id, t("tasks").c.tenant_id == tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "task not found")
    return dict(row)


@router.patch("/{task_id}")
def update_task(task_id: str, body: TaskPatch, user: Principal = Depends(require("tasks", "edit"))):
    vals = _norm(body.model_dump(exclude_unset=True))
    if not vals:
        return {"ok": True}
    _reject_null_required(vals, ("title",))
    with engine.begin() as conn:
        cur = _get_task(conn, user.tenant_id, task_id)
        # validate the recurrence shape against the MERGED row: clearing `frequency` alone
        # (leaving a now-orphaned interval_count behind) must still be caught before it
        # reaches tasks_recurrence_shape as a raw IntegrityError.
        _validate_recurrence({**cur, **vals})
        _validate_links(conn, user.tenant_id, vals)
        conn.execute(update(t("tasks")).where(t("tasks").c.id == task_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.updated", entity_type="task", entity_id=task_id, detail=vals)
    return {"ok": True}


@router.post("/{task_id}/pause")
def pause_task(task_id: str, user: Principal = Depends(require("tasks", "edit"))):
    with engine.begin() as conn:
        cur = _get_task(conn, user.tenant_id, task_id)
        if cur["status"] == "paused":
            raise HTTPException(409, "this task is already paused")
        if cur["status"] == "completed":
            raise HTTPException(409, "a completed task cannot be paused")
        conn.execute(update(t("tasks")).where(t("tasks").c.id == task_id).values(status="paused"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.paused", entity_type="task", entity_id=task_id)
    return {"ok": True}


@router.post("/{task_id}/resume")
def resume_task(task_id: str, user: Principal = Depends(require("tasks", "edit"))):
    with engine.begin() as conn:
        cur = _get_task(conn, user.tenant_id, task_id)
        if cur["status"] == "active":
            raise HTTPException(409, "this task is already active")
        if cur["status"] == "completed":
            raise HTTPException(409, "a completed task cannot be resumed")
        conn.execute(update(t("tasks")).where(t("tasks").c.id == task_id).values(status="active"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.resumed", entity_type="task", entity_id=task_id)
    return {"ok": True}


@router.delete("/{task_id}")
def delete_task(task_id: str, user: Principal = Depends(require("tasks", "delete"))):
    """Refuses to delete a task with any completed run.

    `task_runs.task_id` is CASCADE, so a plain DELETE would silently erase every completed
    run's notes and evidence link along with it — the same compliance-record-loss that
    P4-S5's controls avoid by retiring rather than hard-deleting. There is no RESTRICT FK to
    catch this for us (CASCADE never raises), so it's an explicit check, not a backstop.
    A task with only pending/overdue/skipped runs carries no completion history and is safe
    to remove outright; pause is the tool for "stop this without losing what it already did".
    """
    runs = t("task_runs")
    with engine.begin() as conn:
        _get_task(conn, user.tenant_id, task_id)
        if conn.execute(select(runs.c.id).where(
                runs.c.task_id == task_id, runs.c.status == "done")).first() is not None:
            raise HTTPException(
                409, "this task has completed runs and cannot be deleted — pause it instead")
        conn.execute(sqldelete(t("tasks")).where(t("tasks").c.id == task_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.deleted", entity_type="task", entity_id=task_id)
    return {"ok": True}


class CompleteIn(StrictModel):
    evidence_id: str | None = None
    notes: str | None = None


@router.post("/{task_id}/runs/{run_id}/complete")
def complete(task_id: str, run_id: str, body: CompleteIn,
             user: Principal = Depends(require("tasks", "edit"))):
    with engine.begin() as conn:
        member_id = _member_id(conn, user.tenant_id, user.user_id)
        if body.evidence_id:  # must be tenant's evidence
            ev = t("evidence")
            if conn.execute(select(ev.c.id).where(
                    ev.c.id == body.evidence_id,
                    ev.c.tenant_id == user.tenant_id)).first() is None:
                raise HTTPException(404, "evidence not found")
        result = tasks_engine.complete_run(
            conn, user.tenant_id, task_id, run_id, member_id, body.evidence_id, body.notes)
    if not result:
        raise HTTPException(404, "run not found")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.run_completed", entity_type="task", entity_id=task_id,
                 detail={"evidence_id": body.evidence_id})
    return {"completed": True, **result}
