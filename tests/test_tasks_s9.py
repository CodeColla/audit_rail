"""P4-S9 — task recurrence: frequency/interval, PATCH/DELETE, pause/resume, dormant links.

The engine had exactly one recurrence shape before this sprint — "every N months" — and no
way to edit, remove, pause or resume a task once created. Two dormant columns
(`assignee_person_id`, `document_id`) already had FKs and no API writer. Building the
frequency/interval path also surfaced a real bug the schema comment on `task_runs` had
flagged and left for whoever touched the engine next: `complete_run()` anchored the next
occurrence on TODAY instead of the run's own `due_at`, so finishing something early
permanently dragged its whole future schedule later.
"""

import datetime as dt
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_registers import _h, _person, _tid
from tests.test_identity import uniq_gst
from api import tasks_engine


def engine_conn():
    from api.database import engine
    return engine.connect()

TODAY = dt.date.today()


def iso(days=0):
    return (TODAY + dt.timedelta(days=days)).isoformat()


def _task(app_client, h, **over):
    body = {"title": f"Task {uuid.uuid4().hex[:5]}", **over}
    r = app_client.post("/api/tasks", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _risk(app_client, h, **over):
    body = {"title": f"Risk {uuid.uuid4().hex[:5]}", **over}
    r = app_client.post("/api/risks", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _other_org(app_client):
    r = app_client.post("/api/auth/signup", json={
        "full_name": "Outsider", "email": f"out-{uuid.uuid4().hex[:8]}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Other {uuid.uuid4().hex[:6]}",
        "gst_number": uniq_gst()})
    assert r.status_code == 201, r.text
    return r.json()["tenant_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------- next_occurrence (pure)

def test_next_occurrence_daily_and_weekly_are_day_arithmetic():
    assert tasks_engine.next_occurrence(
        "2026-03-01", frequency="DAILY", interval_count=5, cadence_months=None) == "2026-03-06"
    assert tasks_engine.next_occurrence(
        "2026-03-01", frequency="WEEKLY", interval_count=2, cadence_months=None) == "2026-03-15"


def test_next_occurrence_monthly_quarterly_yearly_are_add_months_multiples():
    assert tasks_engine.next_occurrence(
        "2026-01-31", frequency="MONTHLY", interval_count=1, cadence_months=None) == "2026-02-28"
    assert tasks_engine.next_occurrence(
        "2026-01-15", frequency="QUARTERLY", interval_count=1, cadence_months=None) == "2026-04-15"
    assert tasks_engine.next_occurrence(
        "2026-01-15", frequency="QUARTERLY", interval_count=2, cadence_months=None) == "2026-07-15"
    assert tasks_engine.next_occurrence(
        "2026-02-15", frequency="YEARLY", interval_count=1, cadence_months=None) == "2027-02-15"


def test_next_occurrence_falls_back_to_legacy_cadence_months():
    assert tasks_engine.next_occurrence(
        "2026-01-15", frequency=None, interval_count=None, cadence_months=3) == "2026-04-15"


def test_next_occurrence_with_neither_is_one_off():
    assert tasks_engine.next_occurrence(
        "2026-01-15", frequency=None, interval_count=None, cadence_months=None) is None


# ---------------------------------------------------------------- create + validation

def test_create_task_with_a_frequency(app_client):
    h = _h(app_client)
    tid = _task(app_client, h, frequency="WEEKLY", interval_count=2, next_due_at=iso(7))
    det = app_client.get(f"/api/tasks/{tid}", headers=h).json()
    assert det["frequency"] == "WEEKLY" and det["interval_count"] == 2
    assert det["cadence_months"] is None


def test_frequency_and_interval_must_travel_together(app_client):
    h = _h(app_client)
    for body in ({"title": "x", "frequency": "WEEKLY"}, {"title": "x", "interval_count": 2}):
        r = app_client.post("/api/tasks", headers=h, json=body)
        assert r.status_code == 400, r.text
        assert "together" in r.json()["detail"]


def test_bad_frequency_value_is_400(app_client):
    h = _h(app_client)
    r = app_client.post("/api/tasks", headers=h, json={
        "title": "x", "frequency": "FORTNIGHTLY", "interval_count": 1})
    assert r.status_code == 400 and "frequency must be one of" in r.json()["detail"]


def test_zero_or_negative_interval_is_400(app_client):
    h = _h(app_client)
    for n in (0, -1):
        r = app_client.post("/api/tasks", headers=h, json={
            "title": "x", "frequency": "DAILY", "interval_count": n})
        assert r.status_code == 400, n


def test_cannot_set_both_frequency_and_cadence_months(app_client):
    h = _h(app_client)
    r = app_client.post("/api/tasks", headers=h, json={
        "title": "x", "frequency": "MONTHLY", "interval_count": 1, "cadence_months": 3})
    assert r.status_code == 400 and "not both" in r.json()["detail"]


def test_blank_title_is_400_not_500(app_client):
    h = _h(app_client)
    r = app_client.post("/api/tasks", headers=h, json={"title": "   "})
    assert r.status_code == 400 and "title" in r.json()["detail"]


# ---------------------------------------------------------------- dormant links wired

def test_assignee_person_and_document_and_risk_are_settable(app_client):
    from api.database import engine
    h = _h(app_client)
    tid_org = _tid(engine)
    pid = _person(engine, tid_org, "Reviewer")
    rid = _risk(app_client, h)
    doc = app_client.post("/api/documents", headers=h, json={
        "title": f"Policy {uuid.uuid4().hex[:5]}", "owner_person_id": pid}).json()["id"]

    task = _task(app_client, h, assignee_person_id=pid, document_id=doc, risk_id=rid)
    det = app_client.get(f"/api/tasks/{task}", headers=h).json()
    assert det["assignee_person_id"] == pid
    assert det["document_id"] == doc
    assert det["risk_id"] == rid


def test_a_cross_tenant_link_is_400_not_500(app_client):
    """ADVERSARIAL. Every one of these FKs is composite (col, tenant_id) — a foreign id
    would otherwise reach the database as an uncaught IntegrityError."""
    from api.database import engine
    h = _h(app_client)
    _otid, oh = _other_org(app_client)
    foreign_risk = _risk(app_client, oh)
    r = app_client.post("/api/tasks", headers=h, json={"title": "x", "risk_id": foreign_risk})
    assert r.status_code == 400 and "risk not found" in r.json()["detail"]

    foreign_person = _person(engine, _tid(engine), "Someone")  # exists, but wrong org check below
    # simplest cross-tenant person: create one in the OTHER org's tenant
    with engine.begin() as c:
        opid = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) VALUES "
                          "(:i,:t,'Foreign','f-" + uuid.uuid4().hex[:6] + "@x.example')"),
                  {"i": opid, "t": _otid})
    r2 = app_client.post("/api/tasks", headers=h, json={
        "title": "x", "assignee_person_id": opid})
    assert r2.status_code == 400 and "person not found" in r2.json()["detail"]


def test_deleting_a_risk_detaches_its_task_instead_of_blocking_the_delete(app_client):
    """The dormant-RESTRICT-FK trap that bit P4-S7 twice. `tasks.risk_id` is deliberately
    SET NULL, not RESTRICT: DELETE /risks/{id} hard-deletes, and the first task ever linked
    to a risk must not turn that delete into a 500."""
    h = _h(app_client)
    rid = _risk(app_client, h)
    task = _task(app_client, h, risk_id=rid)
    assert app_client.delete(f"/api/risks/{rid}", headers=h).status_code == 200
    det = app_client.get(f"/api/tasks/{task}", headers=h).json()
    assert det["risk_id"] is None


# ---------------------------------------------------------------- PATCH

def test_patch_updates_fields(app_client):
    h = _h(app_client)
    task = _task(app_client, h, title="Before")
    r = app_client.patch(f"/api/tasks/{task}", headers=h, json={
        "title": "After", "description": "Now with detail."})
    assert r.status_code == 200, r.text
    det = app_client.get(f"/api/tasks/{task}", headers=h).json()
    assert det["title"] == "After" and det["description"] == "Now with detail."


def test_patch_with_empty_body_is_a_noop(app_client):
    h = _h(app_client)
    task = _task(app_client, h, title="Untouched")
    assert app_client.patch(f"/api/tasks/{task}", headers=h, json={}).status_code == 200
    assert app_client.get(f"/api/tasks/{task}", headers=h).json()["title"] == "Untouched"


def test_patch_clearing_frequency_without_interval_is_400(app_client):
    """ADVERSARIAL. Validates against the MERGED row: the task already has frequency +
    interval_count set; a PATCH touching only `frequency` would otherwise orphan
    interval_count and reach `tasks_recurrence_shape` as an uncaught IntegrityError."""
    h = _h(app_client)
    task = _task(app_client, h, frequency="MONTHLY", interval_count=1)
    r = app_client.patch(f"/api/tasks/{task}", headers=h, json={"frequency": None})
    assert r.status_code == 400 and "together" in r.json()["detail"]


def test_patch_can_clear_both_recurrence_fields_at_once(app_client):
    h = _h(app_client)
    task = _task(app_client, h, frequency="MONTHLY", interval_count=1)
    r = app_client.patch(f"/api/tasks/{task}", headers=h, json={
        "frequency": None, "interval_count": None})
    assert r.status_code == 200, r.text
    det = app_client.get(f"/api/tasks/{task}", headers=h).json()
    assert det["frequency"] is None and det["interval_count"] is None


def test_patch_blank_title_is_400_not_500(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    r = app_client.patch(f"/api/tasks/{task}", headers=h, json={"title": "  "})
    assert r.status_code == 400


def test_patch_another_tenants_task_is_404(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    _tid2, oh = _other_org(app_client)
    assert app_client.patch(f"/api/tasks/{task}", headers=oh,
                            json={"title": "stolen"}).status_code == 404


# ---------------------------------------------------------------- pause / resume

def test_pause_then_resume(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    assert app_client.post(f"/api/tasks/{task}/pause", headers=h).status_code == 200
    assert app_client.get(f"/api/tasks/{task}", headers=h).json()["status"] == "paused"
    assert app_client.post(f"/api/tasks/{task}/resume", headers=h).status_code == 200
    assert app_client.get(f"/api/tasks/{task}", headers=h).json()["status"] == "active"


def test_pausing_an_already_paused_task_is_409(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    app_client.post(f"/api/tasks/{task}/pause", headers=h)
    r = app_client.post(f"/api/tasks/{task}/pause", headers=h)
    assert r.status_code == 409 and "already paused" in r.json()["detail"]


def test_resuming_an_active_task_is_409(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    r = app_client.post(f"/api/tasks/{task}/resume", headers=h)
    assert r.status_code == 409 and "already active" in r.json()["detail"]


def test_paused_task_is_excluded_from_the_default_list(app_client):
    h = _h(app_client)
    task = _task(app_client, h, title=f"Pauseme {uuid.uuid4().hex[:5]}")
    app_client.post(f"/api/tasks/{task}/pause", headers=h)
    ids = [t["id"] for t in app_client.get("/api/tasks", headers=h).json()]
    assert task not in ids
    ids_paused = [t["id"] for t in
                  app_client.get("/api/tasks?status=paused", headers=h).json()]
    assert task in ids_paused
    ids_all = [t["id"] for t in app_client.get("/api/tasks?status=all", headers=h).json()]
    assert task in ids_all


def test_a_paused_tasks_overdue_run_does_not_flip_or_notify(app_client):
    """The other half of the pause guarantee. Before P4-S9, `mark_overdue` ignored task
    status entirely, so a paused task's stale run still flipped to overdue and still
    queued a notification every maintenance pass — pausing a task did not actually
    silence it."""
    from api.database import engine
    h = _h(app_client)
    tid = _tid(engine)
    member = None
    with engine.connect() as c:
        member = c.execute(sqltext(
            "SELECT id FROM tenant_members WHERE tenant_id=:t LIMIT 1"), {"t": tid}).scalar()
    task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO tasks (id,tenant_id,title,assignee_member_id,next_due_at,status,"
            "created_at) VALUES (:i,:t,'Paused overdue drill',:m,:d,'paused',now_iso())"),
            {"i": task_id, "t": tid, "m": member, "d": iso(-5)})
        c.execute(sqltext("INSERT INTO task_runs (id,task_id,due_at,status) "
                          "VALUES (:i,:tk,:d,'pending')"), {"i": run_id, "tk": task_id, "d": iso(-5)})

    app_client.post("/api/tasks/generate", headers=h)

    with engine.connect() as c:
        run_status = c.execute(sqltext(
            "SELECT status FROM task_runs WHERE id=:i"), {"i": run_id}).scalar()
    assert run_status == "pending", "a paused task's run must not flip to overdue"

    d = app_client.get("/api/dashboard", headers=h).json()
    assert not any(t["task_id"] == task_id for t in d["queues"]["overdue_tasks"])


def test_pausing_and_resuming_another_tenants_task_is_404(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    _tid2, oh = _other_org(app_client)
    assert app_client.post(f"/api/tasks/{task}/pause", headers=oh).status_code == 404
    assert app_client.post(f"/api/tasks/{task}/resume", headers=oh).status_code == 404


# ---------------------------------------------------------------- delete

def test_deleting_a_task_with_no_completed_runs(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    assert app_client.delete(f"/api/tasks/{task}", headers=h).status_code == 200
    assert app_client.get(f"/api/tasks/{task}", headers=h).status_code == 404


def test_deleting_a_task_with_a_completed_run_is_409(app_client):
    """`task_runs.task_id` is CASCADE, not RESTRICT — a plain delete would silently erase
    the completed run's notes and evidence link with no database error to catch. The guard
    has to be explicit application logic, matching why controls retire instead of delete."""
    h = _h(app_client)
    task = _task(app_client, h, next_due_at=iso(1))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]
    app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h, json={})

    r = app_client.delete(f"/api/tasks/{task}", headers=h)
    assert r.status_code == 409 and "completed runs" in r.json()["detail"]
    assert app_client.get(f"/api/tasks/{task}", headers=h).status_code == 200


def test_deleting_another_tenants_task_is_404(app_client):
    h = _h(app_client)
    task = _task(app_client, h)
    _tid2, oh = _other_org(app_client)
    assert app_client.delete(f"/api/tasks/{task}", headers=oh).status_code == 404
    assert app_client.get(f"/api/tasks/{task}", headers=h).status_code == 200


# ---------------------------------------------------------------- complete_run anchoring

def test_completing_a_run_early_anchors_on_the_runs_due_date_not_today(app_client):
    """THE regression this sprint exists to fix. task_runs.due_at's schema comment records
    the old behavior as deliberate-for-now and asks for exactly this the day the engine's
    recurrence model changes: anchor to due_at, not today. Anchoring on today meant
    finishing something two weeks early pushed the ENTIRE future schedule two weeks late,
    permanently — every early completion drifted the calendar forward a little further.
    """
    h = _h(app_client)
    task = _task(app_client, h, frequency="MONTHLY", interval_count=1, next_due_at=iso(14))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]

    # completed WELL before the 14-day-out due date
    r = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h, json={})
    assert r.status_code == 200, r.text
    # anchored on due_at (today+14): next occurrence is due_at + 1 month, NOT today + 1 month
    assert r.json()["next_due_at"] == tasks_engine.next_occurrence(
        iso(14), frequency="MONTHLY", interval_count=1, cadence_months=None)
    assert r.json()["next_due_at"] != tasks_engine.next_occurrence(
        iso(0), frequency="MONTHLY", interval_count=1, cadence_months=None)


def test_completing_a_very_late_run_can_open_the_next_one_already_overdue(app_client):
    """Documented consequence of anchoring on due_at: a review finished months late does
    not get treated as "on time because you got to it eventually" — the occurrence that
    was actually due in between was, in fact, missed."""
    h = _h(app_client)
    task = _task(app_client, h, frequency="MONTHLY", interval_count=1, next_due_at=iso(-100))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]
    r = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h, json={})
    assert r.json()["next_due_at"] < iso(0), "the next occurrence should already be in the past"


def test_completing_a_one_off_task_marks_it_completed(app_client):
    h = _h(app_client)
    task = _task(app_client, h, next_due_at=iso(1))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]
    r = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h, json={})
    assert r.json()["next_due_at"] is None
    det = app_client.get(f"/api/tasks/{task}", headers=h).json()
    assert det["status"] == "completed"
    # and it drops out of the default (active) list
    assert task not in [t["id"] for t in app_client.get("/api/tasks", headers=h).json()]


def test_complete_requires_the_run_to_belong_to_the_named_task(app_client):
    """The path's task_id used to be decorative — any task in the tenant with a real
    run_id would complete regardless of what the URL claimed. Now a mismatch is a clean
    404 instead of a silently mislabelled activity-log entry."""
    h = _h(app_client)
    task_a = _task(app_client, h, next_due_at=iso(1))
    task_b = _task(app_client, h, next_due_at=iso(1))
    run_b = app_client.get(f"/api/tasks/{task_b}", headers=h).json()["runs"][0]["id"]

    r = app_client.post(f"/api/tasks/{task_a}/runs/{run_b}/complete", headers=h, json={})
    assert r.status_code == 404
    # task_b's run is untouched
    assert app_client.get(f"/api/tasks/{task_b}", headers=h).json()["runs"][0]["status"] == "pending"


# ────────────────────────────────────────────────── P5-S3: evidence produced by the task

def test_upload_then_complete_attaches_fresh_evidence(app_client):
    """The S3 flow. The proof of a completed task usually does not exist in the vault yet —
    the activity log for 2026-07-31 shows a task being closed against a three-day-old
    unrelated PDF because picking was the only option.

    Deliberately two existing calls rather than a multipart `complete`: FastAPI cannot serve
    JSON and multipart on one route, so keeping the `evidence_id` contract working means the
    upload happens separately. This pins the pair the UI now depends on.
    """
    h = _h(app_client)
    task = _task(app_client, h, next_due_at=iso(1))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]

    up = app_client.post("/api/evidence", headers=h,
                         data={"evidence_type": "report", "title": "Access review Q3"},
                         files={"file": ("review.txt", b"reviewed", "text/plain")})
    assert up.status_code == 201, up.text
    ev_id = up.json()["id"]

    done = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h,
                           json={"evidence_id": ev_id, "notes": "done"})
    assert done.status_code == 200, done.text

    # `task_runs.status` vocabulary is lowercase ('pending'|'done'|'overdue'|'skipped'),
    # per db/schema.sql — not the uppercase used elsewhere in the app.
    runs = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"]
    done_run = next(r for r in runs if r["id"] == run_id)
    assert done_run["status"] == "done"

    # the freshly uploaded artifact is the one now attached to the run
    with engine_conn() as c:
        linked = c.execute(sqltext("SELECT evidence_id FROM task_runs WHERE id = :r"),
                           {"r": run_id}).scalar()
    assert linked == ev_id


def test_complete_without_any_evidence_still_works(app_client):
    """S3 added an upload option; it must stay OPTIONAL. Not every task produces a file."""
    h = _h(app_client)
    task = _task(app_client, h, next_due_at=iso(1))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]
    r = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h,
                        json={"evidence_id": None, "notes": "nothing to attach"})
    assert r.status_code == 200, r.text


def test_completing_with_another_tenants_evidence_is_refused(app_client):
    """Upload-then-complete means the client chooses the id it sends. That id is still
    validated server-side — the UI is not the boundary."""
    h = _h(app_client)
    _other_tid, other_h = _other_org(app_client)
    stolen = app_client.post("/api/evidence", headers=other_h,
                             data={"evidence_type": "report", "title": "theirs"},
                             files={"file": ("x.txt", b"x", "text/plain")})
    assert stolen.status_code == 201, stolen.text

    task = _task(app_client, h, next_due_at=iso(1))
    run_id = app_client.get(f"/api/tasks/{task}", headers=h).json()["runs"][0]["id"]
    r = app_client.post(f"/api/tasks/{task}/runs/{run_id}/complete", headers=h,
                        json={"evidence_id": stolen.json()["id"]})
    assert r.status_code in (400, 403, 404), r.text
