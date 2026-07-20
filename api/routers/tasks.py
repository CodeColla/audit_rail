"""Tasks & compliance calendar (M5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, insert, select

from api import activity, tasks_engine
from api.auth import Principal, get_current_user, require_roles
from api.database import engine, get_conn, t
from api.util import IsoDate, now_iso, today_iso

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


@router.post("/generate")
def generate(user: Principal = Depends(require_roles("admin", "manager"))):
    with engine.begin() as conn:
        created = tasks_engine.generate_tasks(conn, user.tenant_id)
        flipped = tasks_engine.mark_overdue(conn)
        retired = tasks_engine.deactivate_expired_people(conn)
    return {"tasks_created": created, "runs_marked_overdue": flipped,
            "people_deactivated": retired}


class TaskIn(BaseModel):
    title: str
    description: str | None = None
    cadence_months: int | None = None
    assignee_member_id: str | None = None
    next_due_at: IsoDate = None
    control_id: str | None = None
    policy_id: str | None = None


@router.post("", status_code=201)
def create_task(body: TaskIn, user: Principal = Depends(get_current_user)):
    tid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(insert(t("tasks")).values(
            id=tid, tenant_id=user.tenant_id, control_id=body.control_id,
            policy_id=body.policy_id, title=body.title, description=body.description,
            cadence_months=body.cadence_months, assignee_member_id=body.assignee_member_id,
            next_due_at=body.next_due_at, status="active", created_at=now_iso()))
        if body.next_due_at:
            conn.execute(insert(t("task_runs")).values(
                id=str(uuid.uuid4()), task_id=tid, due_at=body.next_due_at,
                status="pending"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.created", entity_type="task", entity_id=tid)
    return {"id": tid}


@router.get("")
def list_tasks(mine: bool = Query(False), user: Principal = Depends(get_current_user),
               conn=Depends(get_conn)):
    tasks = t("tasks")
    q = select(tasks).where(tasks.c.tenant_id == user.tenant_id,
                            tasks.c.status == "active").order_by(tasks.c.next_due_at)
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
             user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
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
def task_detail(task_id: str, user: Principal = Depends(get_current_user),
                conn=Depends(get_conn)):
    tasks, runs = t("tasks"), t("task_runs")
    task = conn.execute(select(tasks).where(
        tasks.c.id == task_id, tasks.c.tenant_id == user.tenant_id)).mappings().first()
    if task is None:
        raise HTTPException(404, "task not found")
    run_rows = conn.execute(select(runs).where(runs.c.task_id == task_id)
                            .order_by(runs.c.due_at.desc())).mappings().all()
    return {**dict(task), "runs": [dict(r) for r in run_rows]}


class CompleteIn(BaseModel):
    evidence_id: str | None = None
    notes: str | None = None


@router.post("/{task_id}/runs/{run_id}/complete")
def complete(task_id: str, run_id: str, body: CompleteIn,
             user: Principal = Depends(get_current_user)):
    with engine.begin() as conn:
        member_id = _member_id(conn, user.tenant_id, user.user_id)
        if body.evidence_id:  # must be tenant's evidence
            ev = t("evidence")
            if conn.execute(select(ev.c.id).where(
                    ev.c.id == body.evidence_id,
                    ev.c.tenant_id == user.tenant_id)).first() is None:
                raise HTTPException(404, "evidence not found")
        result = tasks_engine.complete_run(
            conn, user.tenant_id, run_id, member_id, body.evidence_id, body.notes)
    if not result:
        raise HTTPException(404, "run not found")
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="task.run_completed", entity_type="task", entity_id=task_id,
                 detail={"evidence_id": body.evidence_id})
    return {"completed": True, **result}
