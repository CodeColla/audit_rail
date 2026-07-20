"""Sprint 1 / M8 — People register.

The DoD from docs/phase3/05-sprint-plan.md, with one correction: the plan says
"create a person with no user_id AND no email → succeeds". The schema makes
`email` NOT NULL, and rightly so — email is the channel a magic-link signing
request travels down (D-SIGN), so a person without one could never attest.
What the D-SIGN premise actually requires is **no login**, which is `user_id`.
"""

import datetime as dt
import io
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token

TODAY = dt.date.today()


def iso(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def test_person_needs_no_login(app_client):
    """The D-SIGN premise: a CMS/field engineer exists with NO user account."""
    h = _h(app_client)
    r = app_client.post("/api/people", headers=h, json={
        "full_name": "Ravi Kumar", "email": "ravi.kumar@kiam.example",
        "department": "Field Ops", "position": "Field Engineer"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    d = app_client.get(f"/api/people/{pid}", headers=h).json()
    assert d["user_id"] is None
    assert d["has_login"] is False
    assert d["effective_state"] == "ACTIVE"


def test_email_is_normalised_and_validated(app_client):
    h = _h(app_client)
    # uppercase is accepted and lowercased (the email_addr domain demands lowercase)
    r = app_client.post("/api/people", headers=h, json={
        "full_name": "Case Test", "email": "Case.Test@KIAM.example"})
    assert r.status_code == 201
    got = app_client.get(f"/api/people/{r.json()['id']}", headers=h).json()
    assert got["email"] == "case.test@kiam.example"
    # junk is a 422 from the validator, not a 500 from the domain
    bad = app_client.post("/api/people", headers=h,
                          json={"full_name": "Bad", "email": "not-an-email"})
    assert bad.status_code == 422


def test_duplicate_email_is_a_friendly_400(app_client):
    h = _h(app_client)
    body = {"full_name": "Dup One", "email": "dup@kiam.example"}
    assert app_client.post("/api/people", headers=h, json=body).status_code == 201
    r = app_client.post("/api/people", headers=h,
                        json={**body, "full_name": "Dup Two"})
    assert r.status_code == 400
    assert "already on the register" in r.json()["detail"]


def test_self_manage_rejected(app_client):
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Solo", "email": "solo@kiam.example"}).json()["id"]
    r = app_client.patch(f"/api/people/{pid}", headers=h, json={"manager_id": pid})
    assert r.status_code == 400
    assert "manage themselves" in r.json()["detail"]


def test_csv_import_reports_bad_rows(app_client):
    """20 good rows + 2 bad ones: bad rows are REPORTED, never silently dropped."""
    h = _h(app_client)
    rows = ["full_name,email,department,position"]
    for i in range(20):
        rows.append(f"Engineer {i},eng{i}@kiam.example,Field Ops,Field Engineer")
    rows.append("Broken Row,not-an-email,Field Ops,Field Engineer")   # bad email
    rows.append(",blank.name@kiam.example,Field Ops,Field Engineer")  # no name
    csv_bytes = "\n".join(rows).encode()

    r = app_client.post("/api/people/import", headers=h,
                        files={"file": ("roster.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 20
    assert body["failed"] == 2
    assert len(body["errors"]) == 2
    assert all("row" in e and "error" in e for e in body["errors"])

    listed = app_client.get("/api/people?department=Field Ops", headers=h).json()
    assert len([p for p in listed if p["source"] == "IMPORT"]) == 20

    depts = app_client.get("/api/people/departments", headers=h).json()
    assert any(d["department"] == "Field Ops" and d["count"] >= 20 for d in depts)


def test_expired_contract_flips_to_inactive(app_client):
    """The register maintains itself — KSL #15 (leaver access)."""
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Leaver", "email": "leaver@kiam.example",
        "contract_end_date": iso(-1)}).json()["id"]

    # reads derive it immediately (contract dates are authoritative)...
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["effective_state"] == "INACTIVE"

    # ...and the maintenance job persists it (scheduler is off in tests)
    gen = app_client.post("/api/tasks/generate", headers=h).json()
    assert gen["people_deactivated"] >= 1
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["state"] == "INACTIVE"

    inactive = app_client.get("/api/people?state=INACTIVE", headers=h).json()
    assert any(p["id"] == pid for p in inactive)


def test_org_chart_builds_the_tree(app_client):
    """VRA #3.1 — organisational chart."""
    h = _h(app_client)
    boss = app_client.post("/api/people", headers=h, json={
        "full_name": "Ops Head", "email": "opshead@kiam.example"}).json()["id"]
    rep = app_client.post("/api/people", headers=h, json={
        "full_name": "Reports To Boss", "email": "reportsto@kiam.example"}).json()["id"]
    assert app_client.patch(f"/api/people/{rep}", headers=h,
                            json={"manager_id": boss}).status_code == 200

    chart = app_client.get("/api/people/org-chart", headers=h).json()
    node = next((n for n in _walk(chart["roots"]) if n["id"] == boss), None)
    assert node is not None
    assert any(r["id"] == rep for r in node["reports"])
    # the report is not also a root
    assert not any(n["id"] == rep for n in chart["roots"])


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["reports"])


def test_cross_tenant_manager_rejected(app_client):
    """The composite FK (manager_id, tenant_id) must stop cross-tenant links."""
    from api.database import engine
    h = _h(app_client)
    other_tenant, other_person = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO tenants VALUES (:i,'Other','other','active',:n)"),
                  {"i": other_tenant, "n": "2026-07-16T00:00:00Z"})
        c.execute(sqltext(
            "INSERT INTO people (id,tenant_id,full_name,email) VALUES (:i,:t,'Foreign',"
            "'foreign@other.example')"), {"i": other_person, "t": other_tenant})
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Ours", "email": "ours@kiam.example"}).json()["id"]
    r = app_client.patch(f"/api/people/{pid}", headers=h,
                         json={"manager_id": other_person})
    assert r.status_code == 400
    assert "manager not found" in r.json()["detail"]


def test_people_requires_auth(app_client):
    assert app_client.get("/api/people").status_code == 401
