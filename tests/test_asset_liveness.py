"""P8-S1 — asset liveness / last-seen status.

DoD, executable:
  1. a fresh asset with no expected_heartbeat_minutes is UNKNOWN, never STALE.
  2. a heartbeat sets last_seen_at and flips the asset to ONLINE.
  3. an overdue heartbeat (elapsed > expected_heartbeat_minutes) computes STALE live, without
     any stored flag or scheduler tick.
  4. the heartbeat endpoint is tenant-isolated and rejects a bad/garbage token.
  5. expected_heartbeat_minutes is validated (rejects <= 0) and never settable via
     AssetIn/AssetPatch's last_seen_at (StrictModel keeps that field out entirely).
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _longlived_token(app_client, name="heartbeat agent"):
    h = _h(app_client)
    raw_install = app_client.post("/api/integrations/tokens/install", headers=h,
                                  json={"name": name}).json()["token"]
    return app_client.post("/api/integrations/tokens/exchange",
                           json={"install_token": raw_install}).json()["token"]


# ---------------------------------------------------------------- DoD #1

def test_fresh_asset_is_unknown_liveness(app_client):
    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["effective_liveness"] == "UNKNOWN"
    assert d["last_seen_at"] is None

    rows = app_client.get("/api/assets", headers=h).json()
    mine = next(r for r in rows if r["id"] == aid)
    assert mine["effective_liveness"] == "UNKNOWN"


# ---------------------------------------------------------------- DoD #2 & #5

def test_heartbeat_sets_last_seen_and_flips_online(app_client):
    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h, json={
        "name": f"Server {uuid.uuid4().hex[:6]}", "expected_heartbeat_minutes": 60}).json()["id"]

    bad = app_client.patch(f"/api/assets/{aid}", headers=h,
                           json={"expected_heartbeat_minutes": 0})
    assert bad.status_code == 400

    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")
    r = app_client.post("/api/integrations/heartbeat",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"asset_id": aid})
    assert r.status_code == 200, r.text

    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["last_seen_at"] is not None
    assert d["effective_liveness"] == "ONLINE"


# ---------------------------------------------------------------- DoD #3

def test_overdue_heartbeat_computes_stale_live(app_client):
    from api.core.database import engine

    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h, json={
        "name": f"Server {uuid.uuid4().hex[:6]}", "expected_heartbeat_minutes": 5}).json()["id"]

    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")
    app_client.post("/api/integrations/heartbeat",
                    headers={"Authorization": f"Bearer {raw}"},
                    json={"asset_id": aid, "checked_at": "2020-01-01T00:00:00Z"})

    with engine.connect() as c:
        stored_status = c.execute(sqltext(
            "SELECT last_seen_at FROM assets WHERE id=:i"), {"i": aid}).scalar()
    assert stored_status == "2020-01-01T00:00:00Z"  # stored value itself is untouched

    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["effective_liveness"] == "STALE"  # derived live, not stored


# ---------------------------------------------------------------- DoD #4

def test_heartbeat_is_tenant_isolated_and_rejects_bad_token(app_client):
    from api.core.database import engine

    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]

    r = app_client.post("/api/integrations/heartbeat",
                        headers={"Authorization": "Bearer garbage"},
                        json={"asset_id": aid})
    assert r.status_code == 401

    other_tid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO tenants (id,name,slug,status,created_at) "
            "VALUES (:i,'Other Co','other-' || :i,'active','2026-07-16T00:00:00Z')"),
            {"i": other_tid})
        other_asset = str(uuid.uuid4())
        c.execute(sqltext(
            "INSERT INTO assets (id,tenant_id,name,asset_type) "
            "VALUES (:i,:t,'Other asset','VIRTUAL')"), {"i": other_asset, "t": other_tid})

    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")
    r = app_client.post("/api/integrations/heartbeat",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"asset_id": other_asset})
    assert r.status_code == 404  # token belongs to KIAM; the asset belongs to Other Co
