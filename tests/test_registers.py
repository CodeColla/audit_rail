"""Sprint 4a / M10a — Registers I: risks, assets, data inventory.

DoD, executable:
  1. inherent/residual scores auto-compute (L×I) and get a band.
  2. a risk links to a control and shows on that control's page (reverse nav).
  3. owners are PEOPLE, not logins.
  4. (content) — we author our own risks; no Probo import to assert here.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _person(engine, tid, name="Owner"):
    pid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) VALUES (:i,:t,:n,:e)"),
                  {"i": pid, "t": tid, "n": name,
                   "e": f"{name.lower()}-{uuid.uuid4().hex[:6]}@kiam.example"})
    return pid


def _control(engine, tid, statement="Access to the CMS is controlled."):
    cid, code = str(uuid.uuid4()), f"AM {uuid.uuid4().hex[:4]}"
    with engine.begin() as c:
        did = c.execute(sqltext("SELECT id FROM domains WHERE tenant_id=:t LIMIT 1"), {"t": tid}).scalar()
        c.execute(sqltext("INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle) "
                          "VALUES (:i,:t,:d,:c,:s,'per_audit')"),
                  {"i": cid, "t": tid, "d": did, "c": code, "s": statement})
    return cid, code


# ---------------------------------------------------------------- DoD #1

def test_scores_and_bands_autocompute(app_client):
    from api.core.database import engine
    h = _h(app_client)
    r = app_client.post("/api/risks", headers=h, json={
        "title": "Unauthorised access to bank CMS",
        "inherent_likelihood": 3, "inherent_impact": 3,
        "residual_likelihood": 1, "residual_impact": 3, "treatment": "MITIGATED"})
    assert r.status_code == 201, r.text
    d = app_client.get(f"/api/risks/{r.json()['id']}", headers=h).json()
    assert d["inherent_score"] == 9 and d["inherent_band"] == "HIGH"      # 3×3
    assert d["residual_score"] == 3 and d["residual_band"] == "LOW"       # 1×3
    assert d["treatment"] == "MITIGATED"


def test_score_range_is_validated(app_client):
    h = _h(app_client)
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "x", "inherent_likelihood": 6}).status_code == 400
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "x", "treatment": "IGNORED"}).status_code == 400


# ---------------------------------------------------------------- DoD #2 (reverse nav)

def test_risk_links_to_control_and_shows_on_control(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    cid, code = _control(engine, tid)
    rid = app_client.post("/api/risks", headers=h, json={
        "title": "CMS credential theft", "inherent_likelihood": 4, "inherent_impact": 4}).json()["id"]

    link = app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "CONTROL", "target_id": cid})
    assert link.status_code == 201

    # forward: the risk shows the linked control
    rd = app_client.get(f"/api/risks/{rid}", headers=h).json()
    assert any(l["target_kind"] == "CONTROL" and l["target_id"] == cid for l in rd["links"])
    assert any(code in (l["label"] or "") for l in rd["links"])

    # reverse: the control's page shows the risk
    cd = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert any(lr["id"] == rid and lr["inherent_score"] == 16 for lr in cd["linked_risks"])


def test_link_guards(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    cid, _ = _control(engine, tid)
    rid = app_client.post("/api/risks", headers=h, json={"title": "r"}).json()["id"]
    # bad kind
    assert app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "PLANET", "target_id": cid}).status_code == 400
    # target not in tenant
    assert app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "CONTROL", "target_id": str(uuid.uuid4())}).status_code == 400
    # duplicate
    assert app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "CONTROL", "target_id": cid}).status_code == 201
    assert app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "CONTROL", "target_id": cid}).status_code == 409


def test_delete_risk_cascades_its_links(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    cid, _ = _control(engine, tid)
    rid = app_client.post("/api/risks", headers=h, json={"title": "temp"}).json()["id"]
    app_client.post(f"/api/risks/{rid}/links", headers=h,
                    json={"target_kind": "CONTROL", "target_id": cid})
    assert app_client.delete(f"/api/risks/{rid}", headers=h).status_code == 200
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM risk_links WHERE risk_id=:r"), {"r": rid}).scalar() == 0
        assert c.execute(sqltext("SELECT count(*) FROM controls WHERE id=:c"), {"c": cid}).scalar() == 1  # control survives


# ---------------------------------------------------------------- DoD #3 (person owner)

def test_owner_must_be_a_person(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "x", "owner_person_id": str(uuid.uuid4())}).status_code == 400
    pid = _person(engine, tid, "Ravi")
    rid = app_client.post("/api/risks", headers=h, json={"title": "owned", "owner_person_id": pid}).json()["id"]
    assert app_client.get(f"/api/risks/{rid}", headers=h).json()["owner_name"] == "Ravi"


# ---------------------------------------------------------------- assets + data

def test_asset_criticality_and_data_classification(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    dname = f"Bank customer data {uuid.uuid4().hex[:5]}"
    app_client.post("/api/data-items", headers=h,
                    json={"name": dname, "classification": "CONFIDENTIAL"})
    aid = app_client.post("/api/assets", headers=h, json={
        "name": "CMS Server", "asset_type": "VIRTUAL", "criticality": "CRITICAL",
        "data_types_stored": [dname]}).json()["id"]
    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["criticality"] == "CRITICAL"
    assert d["data"] == [{"name": dname, "classification": "CONFIDENTIAL"}]


def test_asset_validation(app_client):
    h = _h(app_client)
    assert app_client.post("/api/assets", headers=h,
                           json={"name": "x", "criticality": "APOCALYPTIC"}).status_code == 400
    assert app_client.post("/api/assets", headers=h,
                           json={"name": "x", "asset_type": "CLOUDY"}).status_code == 400


def test_data_item_crud_and_classification(app_client):
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    pid = _person(engine, tid, "DataOwner")
    did = app_client.post("/api/data-items", headers=h, json={
        "name": f"PAN numbers {uuid.uuid4().hex[:5]}", "classification": "SECRET",
        "owner_person_id": pid, "retention_note": "7 years"}).json()["id"]
    items = app_client.get("/api/data-items", headers=h).json()
    mine = next(i for i in items if i["id"] == did)
    assert mine["classification"] == "SECRET" and mine["owner_name"] == "DataOwner"
    assert app_client.patch(f"/api/data-items/{did}", headers=h,
                            json={"classification": "NUCLEAR"}).status_code == 400
    assert app_client.patch(f"/api/data-items/{did}", headers=h,
                            json={"classification": "CONFIDENTIAL"}).status_code == 200


def test_reference_must_be_unique(app_client):
    h = _h(app_client)
    ref = f"R-{uuid.uuid4().hex[:6]}"
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "a", "reference": ref}).status_code == 201
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "b", "reference": ref}).status_code == 409


# ---------------------------------------------------------------- adversarial-review fixes

def test_patch_null_on_required_column_is_400_not_500(app_client):
    """CONFIRMED (review): PATCH {"status": null} used to set a NOT NULL column to null →
    uncaught IntegrityError → 500. Now a clean 400."""
    h = _h(app_client)
    rid = app_client.post("/api/risks", headers=h, json={"title": "r"}).json()["id"]
    assert app_client.patch(f"/api/risks/{rid}", headers=h, json={"status": None}).status_code == 400
    assert app_client.patch(f"/api/risks/{rid}", headers=h, json={"title": None}).status_code == 400
    aid = app_client.post("/api/assets", headers=h, json={"name": "a"}).json()["id"]
    assert app_client.patch(f"/api/assets/{aid}", headers=h, json={"name": None}).status_code == 400
    assert app_client.patch(f"/api/assets/{aid}", headers=h, json={"data_types_stored": None}).status_code == 400


def test_empty_strings_are_coerced_not_500(app_client):
    """CONFIRMED (review): '' bypassed the truthy validators → DB CHECK 500, and ''-references
    collided on UNIQUE. Empty strings now normalise to NULL."""
    h = _h(app_client)
    # empty enum → stored as null, not a 500
    rid = app_client.post("/api/risks", headers=h,
                          json={"title": "r", "treatment": "", "category": ""}).json()["id"]
    assert app_client.get(f"/api/risks/{rid}", headers=h).json()["treatment"] is None
    # two blank references don't collide (both become NULL)
    assert app_client.post("/api/risks", headers=h, json={"title": "a", "reference": ""}).status_code == 201
    assert app_client.post("/api/risks", headers=h, json={"title": "b", "reference": "  "}).status_code == 201


def test_reference_collision_on_patch_is_409_not_500(app_client):
    h = _h(app_client)
    ref = f"R-{uuid.uuid4().hex[:6]}"
    app_client.post("/api/risks", headers=h, json={"title": "a", "reference": ref})
    bid = app_client.post("/api/risks", headers=h, json={"title": "b"}).json()["id"]
    assert app_client.patch(f"/api/risks/{bid}", headers=h, json={"reference": ref}).status_code == 409


def test_duplicate_link_is_blocked_by_the_db(app_client):
    """CONFIRMED (review): the app dedupe was racy with no backing constraint. uq_risk_link_target
    now makes a duplicate (risk, target) impossible even if two requests slip past the pre-check."""
    from api.core.database import engine
    h, tid = _h(app_client), _tid(engine)
    cid, _ = _control(engine, tid)
    rid = app_client.post("/api/risks", headers=h, json={"title": "dup"}).json()["id"]
    assert app_client.post(f"/api/risks/{rid}/links", headers=h,
                           json={"target_kind": "CONTROL", "target_id": cid}).status_code == 201
    with engine.begin() as c:
        try:
            c.execute(sqltext("INSERT INTO risk_links (id,tenant_id,risk_id,target_kind,control_id,created_at) "
                              "VALUES (:i,:t,:r,'CONTROL',:c, now_iso())"),
                      {"i": str(uuid.uuid4()), "t": tid, "r": rid, "c": cid})
            raised = False
        except Exception as e:  # noqa: BLE001
            raised = "uq_risk_link_target" in str(e) or "duplicate key" in str(e).lower()
    assert raised, "the unique index did not reject the duplicate link"


def test_invalid_calendar_date_is_rejected(app_client):
    """CONFIRMED (review): iso_or_none accepted impossible dates. 422, not stored."""
    h = _h(app_client)
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "r", "next_review_at": "2026-13-45"}).status_code == 422
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "r", "next_review_at": "2026-07-20"}).status_code == 201


def test_quantity_upper_bound(app_client):
    h = _h(app_client)
    assert app_client.post("/api/assets", headers=h,
                           json={"name": "a", "quantity": 99999999999}).status_code == 400


def test_registers_require_auth(app_client):
    assert app_client.get("/api/risks").status_code == 401
    assert app_client.get("/api/assets").status_code == 401
    assert app_client.get("/api/data-items").status_code == 401
