"""M5 — recurrence engine, task completion, and dashboard queues."""

import datetime as dt
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token

TODAY = dt.date.today()


def iso(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


def _recurring_control(engine, tenant_id, code):
    cid = str(uuid.uuid4())
    with engine.begin() as c:
        dom = c.execute(sqltext("SELECT id FROM domains WHERE code='AM' AND tenant_id=:t"),
                        {"t": tenant_id}).scalar()
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "recurrence_months,applicability,status,created_at,updated_at) VALUES "
            "(:i,:t,:d,:code,'Quarterly access review','recurring',3,'applicable',"
            "'active','2026-07-11T00:00:00Z','2026-07-11T00:00:00Z')"),
            {"i": cid, "t": tenant_id, "d": dom, "code": code})
    return cid


def test_generate_complete_and_dashboard(app_client):
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    _recurring_control(engine, tid, "TSK 1.a")
    atok = token(app_client, "admin@kiam.example", "secret1")
    ah = {"Authorization": f"Bearer {atok}"}

    # generate tasks from recurring controls (idempotent)
    g = app_client.post("/api/tasks/generate", headers=ah).json()
    assert g["tasks_created"] >= 1
    g2 = app_client.post("/api/tasks/generate", headers=ah).json()
    assert g2["tasks_created"] == 0  # no duplicates

    tasks = app_client.get("/api/tasks", headers=ah).json()
    task = next(t for t in tasks if "Quarterly access review" in t["title"])
    assert task["cadence_months"] == 3
    assert task["next_run"] is not None
    run_id = task["next_run"]["id"]
    task_id = task["id"]

    # complete a run -> rolls forward and opens the next run
    r = app_client.post(f"/api/tasks/{task_id}/runs/{run_id}/complete",
                        headers=ah, json={"notes": "reviewed"}).json()
    assert r["completed"] is True
    assert r["next_due_at"] is not None
    detail = app_client.get(f"/api/tasks/{task_id}", headers=ah).json()
    statuses = sorted(rr["status"] for rr in detail["runs"])
    assert "done" in statuses and "pending" in statuses


def test_overdue_and_dashboard_queue(app_client):
    """A backdated run must surface as overdue in the dashboard."""
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
        member = c.execute(sqltext(
            "SELECT id FROM tenant_members WHERE tenant_id=:t LIMIT 1"),
            {"t": tid}).scalar()
    task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO tasks (id,tenant_id,title,cadence_months,assignee_member_id,"
            "next_due_at,status,created_at) VALUES (:i,:t,'Overdue drill',3,:m,:d,"
            "'active','2026-07-11T00:00:00Z')"),
            {"i": task_id, "t": tid, "m": member, "d": iso(-5)})
        c.execute(sqltext("INSERT INTO task_runs (id,task_id,due_at,status) "
                          "VALUES (:i,:tk,:d,'pending')"),
                  {"i": run_id, "tk": task_id, "d": iso(-5)})
        # an expiring cert + an overdue policy for the other queues
        c.execute(sqltext("INSERT INTO evidence (id,tenant_id,title,evidence_type,"
                          "medium,state,external_url,valid_until,created_at) "
                          "VALUES (:i,:t,'Cert','certificate','LINK','FULFILLED',"
                          "'https://example.test/cert',:v,'2026-07-11T00:00:00Z')"),
                  {"i": str(uuid.uuid4()), "t": tid, "v": iso(5)})
        c.execute(sqltext("INSERT INTO policies (id,tenant_id,title,review_cadence_months,"
                          "next_review_at,status,created_at) VALUES (:i,:t,'ISP',12,:d,"
                          "'active','2026-07-11T00:00:00Z')"),
                  {"i": str(uuid.uuid4()), "t": tid, "d": iso(-3)})

    atok = token(app_client, "admin@kiam.example", "secret1")
    ah = {"Authorization": f"Bearer {atok}"}

    # scheduler is off in tests -> flip overdue explicitly via /generate
    app_client.post("/api/tasks/generate", headers=ah)

    d = app_client.get("/api/dashboard", headers=ah).json()
    q = d["queues"]
    assert any("Overdue drill" == t["title"] for t in q["overdue_tasks"])
    assert any(e["status"] in ("expiring", "expired") for e in q["expiring_evidence"])
    assert any(p["review_status"] in ("overdue", "due_soon") for p in q["documents_due"])
    assert "overall_readiness_pct" in d["kpis"]

    # the overdue flip generated a notification for the assignee
    notes = app_client.get("/api/notifications", headers=ah).json()
    assert any(n["type"] == "task_overdue" for n in notes)
