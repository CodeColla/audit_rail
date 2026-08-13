"""Sprint 4b / M10b — Registers II: third parties (+4th-party tree), agreements,
assessments (expiring), obligations (RBI ↔ controls), incidents (RCA-gated close).

DoD:
  1. a 4th party (vendor's vendor) renders as a nested tree.
  2. an expiring third_party_assessment appears in the existing expiring queue (D-MOAT).
  3. an incident captures root_cause + lessons_learnt (and can't CLOSE without a root cause).
  4. an obligation links to ≥1 control (and shows on the control — feeds SoA in Sprint 7).
"""

import datetime as dt
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _control(engine, tid):
    cid, code = str(uuid.uuid4()), f"AM {uuid.uuid4().hex[:4]}"
    with engine.begin() as c:
        did = c.execute(sqltext("SELECT id FROM domains WHERE tenant_id=:t LIMIT 1"), {"t": tid}).scalar()
        c.execute(sqltext("INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle) "
                          "VALUES (:i,:t,:d,:c,'Vendors are risk-assessed.','per_audit')"),
                  {"i": cid, "t": tid, "d": did, "c": code})
    return cid, code


def _days(n):
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


def _tp(app_client, h, name, parent=None):
    body = {"name": name}
    if parent:
        body["parent_third_party_id"] = parent
    r = app_client.post("/api/third-parties", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- DoD #1 (4th-party tree)

def test_fourth_party_tree_nests(app_client):
    h = _h(app_client)
    a = _tp(app_client, h, "Cloud Vendor A")          # our vendor (3rd party)
    b = _tp(app_client, h, "Hosting Vendor B", parent=a)   # their vendor (4th party)
    c = _tp(app_client, h, "Backup Vendor C", parent=b)    # 5th party
    tree = app_client.get(f"/api/third-parties/{a}/tree", headers=h).json()
    assert tree["id"] == a and len(tree["children"]) == 1
    node_b = tree["children"][0]
    assert node_b["id"] == b and node_b["children"][0]["id"] == c


def test_cycle_is_rejected(app_client):
    h = _h(app_client)
    a = _tp(app_client, h, "V-A")
    b = _tp(app_client, h, "V-B", parent=a)
    # make A a child of B → A would be its own ancestor
    r = app_client.patch(f"/api/third-parties/{a}", headers=h, json={"parent_third_party_id": b})
    assert r.status_code == 400 and "circular" in r.json()["detail"]
    # self-parent too
    assert app_client.patch(f"/api/third-parties/{a}", headers=h,
                            json={"parent_third_party_id": a}).status_code == 400
    # unknown parent
    assert app_client.post("/api/third-parties", headers=h,
                           json={"name": "x", "parent_third_party_id": str(uuid.uuid4())}).status_code == 400


# ---------------------------------------------------------------- DoD #2 (expiring queue)

def test_expiring_assessment_hits_the_dashboard_queue(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h, f"Assessed Vendor {uuid.uuid4().hex[:5]}")
    # one expiring soon, one already expired
    soon = app_client.post(f"/api/third-parties/{tp}/assessments", headers=h,
                           json={"expires_at": _days(10), "outcome": "PASS"}).json()["id"]
    app_client.post(f"/api/third-parties/{tp}/assessments", headers=h,
                    json={"expires_at": _days(-5), "outcome": "PASS_WITH_ACTIONS"})
    dash = app_client.get("/api/dashboard", headers=h).json()
    q = dash["queues"]["expiring_assessments"]
    mine = [x for x in q if x["id"] == soon]
    assert mine and mine[0]["status"] == "expiring"
    assert any(x["status"] == "expired" for x in q)
    # and the existing evidence queue still exists (not regressed)
    assert "expiring_evidence" in dash["queues"]


def test_assessment_enum_validation(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h, f"V {uuid.uuid4().hex[:5]}")
    assert app_client.post(f"/api/third-parties/{tp}/assessments", headers=h,
                           json={"outcome": "MAYBE"}).status_code == 400


# ---------------------------------------------------------------- DoD #3 (incident RCA)

def test_incident_captures_rca_and_gates_close(app_client):
    h = _h(app_client)
    iid = app_client.post("/api/incidents", headers=h, json={
        "title": "CMS brief outage", "severity": "HIGH", "detected_at": _days(-2)}).json()["id"]
    # can't CLOSE without a root cause (schema CHECK, surfaced as a friendly 400)
    r = app_client.patch(f"/api/incidents/{iid}", headers=h, json={"status": "CLOSED"})
    assert r.status_code == 400 and "root cause" in r.json()["detail"]
    # add RCA + lessons, then close
    app_client.patch(f"/api/incidents/{iid}", headers=h, json={
        "root_cause": "expired TLS cert", "lessons_learnt": "add cert-expiry monitoring"})
    assert app_client.patch(f"/api/incidents/{iid}", headers=h, json={"status": "CLOSED"}).status_code == 200
    d = app_client.get(f"/api/incidents/{iid}", headers=h).json()
    assert d["root_cause"] == "expired TLS cert" and d["lessons_learnt"] == "add cert-expiry monitoring"
    assert d["status"] == "CLOSED"


def test_incident_reference_unique(app_client):
    h = _h(app_client)
    ref = f"INC-{uuid.uuid4().hex[:6]}"
    assert app_client.post("/api/incidents", headers=h,
                           json={"title": "a", "reference": ref}).status_code == 201
    assert app_client.post("/api/incidents", headers=h,
                           json={"title": "b", "reference": ref}).status_code == 409


# ---------------------------------------------------------------- DoD #4 (obligation ↔ control)

def test_obligation_links_to_a_control_both_ways(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    cid, code = _control(engine, tid)
    oid = app_client.post("/api/obligations", headers=h, json={
        "requirement": "Outsourcing risk management (RBI IT outsourcing)",
        "regulator": "RBI", "type": "LEGAL"}).json()["id"]
    assert app_client.post(f"/api/obligations/{oid}/controls", headers=h,
                           json={"control_id": cid}).status_code == 201
    # forward: obligation shows the control
    od = app_client.get(f"/api/obligations/{oid}", headers=h).json()
    assert any(c["id"] == cid for c in od["controls"])
    # reverse: control page shows the obligation
    cd = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert any(o["id"] == oid and o["regulator"] == "RBI" for o in cd["linked_obligations"])
    # duplicate link → 409
    assert app_client.post(f"/api/obligations/{oid}/controls", headers=h,
                           json={"control_id": cid}).status_code == 409


def test_obligation_control_must_exist_in_tenant(app_client):
    h = _h(app_client)
    oid = app_client.post("/api/obligations", headers=h, json={"requirement": "x"}).json()["id"]
    assert app_client.post(f"/api/obligations/{oid}/controls", headers=h,
                           json={"control_id": str(uuid.uuid4())}).status_code == 400


# ---------------------------------------------------------------- agreements + guards

def test_agreement_shows_with_expiry_status(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h, f"Agreement Vendor {uuid.uuid4().hex[:5]}")
    app_client.post(f"/api/third-parties/{tp}/agreements", headers=h,
                    json={"kind": "DPA", "valid_until": _days(20)})
    d = app_client.get(f"/api/third-parties/{tp}", headers=h).json()
    assert d["agreements"][0]["kind"] == "DPA"
    assert d["agreements"][0]["expiry_status"] == "expiring"
    # bad kind
    assert app_client.post(f"/api/third-parties/{tp}/agreements", headers=h,
                           json={"kind": "TREATY"}).status_code == 400


def test_patch_null_required_is_400(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h, "Nullable Vendor")
    assert app_client.patch(f"/api/third-parties/{tp}", headers=h,
                            json={"name": None}).status_code == 400
    oid = app_client.post("/api/obligations", headers=h, json={"requirement": "r"}).json()["id"]
    assert app_client.patch(f"/api/obligations/{oid}", headers=h,
                            json={"requirement": None}).status_code == 400


# ---------------------------------------------------------------- adversarial-review fixes

def test_patch_null_array_column_is_400_not_500(app_client):
    """CONFIRMED (review): countries/certifications are text[] NOT NULL; PATCH with an explicit
    null used to 500. Now a clean 400."""
    h = _h(app_client)
    tp = _tp(app_client, h, "Array Vendor")
    assert app_client.patch(f"/api/third-parties/{tp}", headers=h,
                            json={"countries": None}).status_code == 400
    assert app_client.patch(f"/api/third-parties/{tp}", headers=h,
                            json={"certifications": None}).status_code == 400
    # a real value still works
    assert app_client.patch(f"/api/third-parties/{tp}", headers=h,
                            json={"countries": ["IN", "SG"]}).status_code == 200


def test_reparent_is_serialized_but_still_works(app_client):
    """The reparent path now takes a per-tenant advisory lock (pg_advisory_xact_lock) before the
    cycle check to defeat concurrent write-skew. A normal reparent must still work, and a
    sequential cycle is still rejected (the lock only serialises; it never allows a loop)."""
    h = _h(app_client)
    a = _tp(app_client, h, "Lock A")
    b = _tp(app_client, h, "Lock B")
    # valid reparent: B becomes a sub-processor of A
    assert app_client.patch(f"/api/third-parties/{b}", headers=h,
                            json={"parent_third_party_id": a}).status_code == 200
    tree = app_client.get(f"/api/third-parties/{a}/tree", headers=h).json()
    assert any(c["id"] == b for c in tree["children"])
    # and A cannot now become a child of B (that's the cycle, still rejected)
    assert app_client.patch(f"/api/third-parties/{a}", headers=h,
                            json={"parent_third_party_id": b}).status_code == 400


def test_registers_b_require_auth(app_client):
    assert app_client.get("/api/third-parties").status_code == 401
    assert app_client.get("/api/obligations").status_code == 401
    assert app_client.get("/api/incidents").status_code == 401
