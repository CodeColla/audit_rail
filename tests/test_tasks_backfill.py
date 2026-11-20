"""#20 S4 — backfill: attach several files to one past period of a recurring task.

The rule the whole feature rests on: a backfill is not a completion. It must never move
`tasks.next_due_at`, never complete or re-date the pending run, and never change an existing
run's status. Only complete_run advances a schedule.
"""

import datetime as dt
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_identity import uniq_gst

PDF = b"%PDF-1.4\n% backfill test\n"


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _yearly(client, h, next_due="2026-06-15"):
    r = client.post("/api/tasks", headers=h, json={
        "title": f"Backfill {uuid.uuid4().hex[:5]}", "frequency": "YEARLY",
        "interval_count": 1, "next_due_at": next_due})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _files(names):
    return [("files", (n, PDF, "application/pdf")) for n in names]


def _backfill(client, h, task_id, year, names=("a.pdf", "b.pdf", "c.pdf")):
    return client.post(f"/api/tasks/{task_id}/backfill", headers=h,
                       data={"period_year": str(year), "evidence_type": "report"},
                       files=_files(names))


def _schedule(engine, task_id):
    with engine.connect() as c:
        next_due = c.execute(sqltext("SELECT next_due_at FROM tasks WHERE id=:i"),
                             {"i": task_id}).scalar()
        runs = [tuple(r) for r in c.execute(sqltext(
            "SELECT id, due_at, status FROM task_runs WHERE task_id=:i ORDER BY due_at, id"),
            {"i": task_id})]
    return next_due, runs


def test_three_files_attach_to_one_past_year_and_leave_the_schedule_alone(app_client):
    from api.core.database import engine
    h = _h(app_client)
    task_id = _yearly(app_client, h, next_due="2026-06-15")
    before_next, before_runs = _schedule(engine, task_id)

    r = _backfill(app_client, h, task_id, 2024)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run_created"] is True
    assert body["due_at"].startswith("2024-06-15")
    assert len(body["evidence"]) == 3

    after_next, after_runs = _schedule(engine, task_id)
    assert after_next == before_next, "a backfill must not move next_due_at"
    # the original pending run is still there, untouched
    assert [r for r in after_runs if r[0] in {x[0] for x in before_runs}] == before_runs

    with engine.connect() as c:
        linked = c.execute(sqltext(
            "SELECT count(*) FROM task_run_evidence WHERE task_run_id=:r"),
            {"r": body["task_run_id"]}).scalar()
        status = c.execute(sqltext("SELECT status FROM task_runs WHERE id=:r"),
                           {"r": body["task_run_id"]}).scalar()
    assert linked == 3
    assert status == "done"


def test_a_second_backfill_for_the_same_year_reuses_its_run(app_client):
    from api.core.database import engine
    h = _h(app_client)
    task_id = _yearly(app_client, h, next_due="2026-03-01")
    first = _backfill(app_client, h, task_id, 2025, names=("one.pdf",)).json()
    second = _backfill(app_client, h, task_id, 2025, names=("two.pdf",)).json()
    assert second["task_run_id"] == first["task_run_id"]
    assert second["run_created"] is False
    with engine.connect() as c:
        count = c.execute(sqltext(
            "SELECT count(*) FROM task_run_evidence WHERE task_run_id=:r"),
            {"r": first["task_run_id"]}).scalar()
    assert count == 2


def test_a_normal_complete_still_advances_after_a_backfill(app_client):
    from api.core.database import engine
    h = _h(app_client)
    task_id = _yearly(app_client, h, next_due="2026-06-15")
    _backfill(app_client, h, task_id, 2024, names=("old.pdf",))
    _, runs = _schedule(engine, task_id)
    pending = [r for r in runs if r[2] == "pending"]
    assert len(pending) == 1
    r = app_client.post(f"/api/tasks/{task_id}/runs/{pending[0][0]}/complete",
                        headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["next_due_at"].startswith("2027-06-15")


def test_future_years_are_refused(app_client):
    h = _h(app_client)
    task_id = _yearly(app_client, h)
    future = dt.date.today().year + 1
    r = _backfill(app_client, h, task_id, future, names=("x.pdf",))
    assert r.status_code == 400


def test_one_off_tasks_have_no_periods_to_backfill(app_client):
    h = _h(app_client)
    r = app_client.post("/api/tasks", headers=h, json={"title": "One-off"})
    assert r.status_code == 201, r.text
    r = _backfill(app_client, h, r.json()["id"], 2024, names=("x.pdf",))
    assert r.status_code == 400


def test_another_organisations_task_is_not_found(app_client):
    from api.core.database import engine  # noqa: F401  (engine import keeps fixture order)
    h = _h(app_client)
    task_id = _yearly(app_client, h)
    other = app_client.post("/api/auth/signup", json={
        "full_name": "Other Org Owner", "email": f"bf-{uuid.uuid4().hex[:8]}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"BF Org {uuid.uuid4().hex[:6]}",
        "gst_number": uniq_gst()})
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    r = _backfill(app_client, oh, task_id, 2024, names=("steal.pdf",))
    assert r.status_code == 404
