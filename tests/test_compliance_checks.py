"""P8-S3a — compliance-config check monitoring (Vanta/Drata-style).

DoD, executable:
  1. a batch of checks upserts (insert then update-in-place) on (tenant, asset, check_key).
  2. effective_status is derived live: overdue -> STALE, regardless of the stored PASS/FAIL.
  3. source is server-set from the token's name and cannot be spoofed by the request body.
  4. asset_detail surfaces the asset's checks; control_detail surfaces a cross-asset rollup.
  5. tenant isolation and input validation (bad status/asset/control -> 400/404, not 500).
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _longlived_token(app_client, name):
    h = _h(app_client)
    raw_install = app_client.post("/api/integrations/tokens/install", headers=h,
                                  json={"name": name}).json()["token"]
    return app_client.post("/api/integrations/tokens/exchange",
                           json={"install_token": raw_install}).json()["token"]


def _control(engine, tid, statement="MFA is enforced on all admin accounts."):
    cid, code = str(uuid.uuid4()), f"AM {uuid.uuid4().hex[:4]}"
    with engine.begin() as c:
        did = c.execute(sqltext(
            "SELECT id FROM domains WHERE tenant_id=:t LIMIT 1"), {"t": tid}).scalar()
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle) "
            "VALUES (:i,:t,:d,:c,:s,'per_audit')"),
            {"i": cid, "t": tid, "d": did, "c": code, "s": statement})
    return cid


# ---------------------------------------------------------------- DoD #1 & #3

def test_batch_upsert_inserts_then_updates_in_place(app_client):
    from api.core.database import engine

    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    raw = _longlived_token(app_client, f"aws-config-{uuid.uuid4().hex[:6]}")
    hh = {"Authorization": f"Bearer {raw}"}

    # `source` isn't even an accepted field on the request — StrictModel rejects it outright
    # (422) rather than silently ignoring it, which is a stronger guarantee than "server
    # overwrites whatever you send."
    spoofed = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": aid, "check_key": "mfa_enabled", "check_label": "MFA enabled",
         "status": "PASS", "source": "spoofed-name"}]})
    assert spoofed.status_code == 422

    r1 = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": aid, "check_key": "mfa_enabled", "check_label": "MFA enabled",
         "status": "PASS"},
        {"asset_id": aid, "check_key": "disk_encryption", "check_label": "Disk encryption",
         "status": "FAIL"},
    ]})
    assert r1.status_code == 200, r1.text
    assert len(r1.json()["checks"]) == 2

    with engine.connect() as c:
        rows = c.execute(sqltext(
            "SELECT check_key, status, source FROM compliance_checks WHERE asset_id=:a"),
            {"a": aid}).mappings().all()
    assert len(rows) == 2
    assert all(r["source"] != "spoofed-name" for r in rows)  # server-set from the token

    # re-post the same check_key with a different status: must UPDATE in place, not duplicate
    r2 = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": aid, "check_key": "mfa_enabled", "check_label": "MFA enabled",
         "status": "FAIL"},
    ]})
    assert r2.status_code == 200, r2.text
    with engine.connect() as c:
        rows = c.execute(sqltext(
            "SELECT status FROM compliance_checks WHERE asset_id=:a AND check_key='mfa_enabled'"),
            {"a": aid}).mappings().all()
    assert len(rows) == 1 and rows[0]["status"] == "FAIL"


# ---------------------------------------------------------------- DoD #2

def test_effective_status_is_stale_when_overdue(app_client):
    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")

    app_client.post("/api/integrations/checks", headers={"Authorization": f"Bearer {raw}"},
                    json={"checks": [{
                        "asset_id": aid, "check_key": "patch_level", "check_label": "Patch level",
                        "status": "PASS", "expected_interval_minutes": 60,
                        "checked_at": "2020-01-01T00:00:00Z"}]})

    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    chk = next(c for c in d["compliance_checks"] if c["check_key"] == "patch_level")
    assert chk["status"] == "PASS"              # stored value untouched
    assert chk["effective_status"] == "STALE"    # derived live, overrides PASS


# ---------------------------------------------------------------- DoD #4

def test_asset_detail_and_control_rollup_surface_checks(app_client):
    from api.core.database import engine

    h, tid = _h(app_client), _tid(engine)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    cid = _control(engine, tid)
    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")

    app_client.post("/api/integrations/checks", headers={"Authorization": f"Bearer {raw}"},
                    json={"checks": [{
                        "asset_id": aid, "control_id": cid, "check_key": "mfa_enabled",
                        "check_label": "MFA enabled", "status": "PASS"}]})

    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert any(c["check_key"] == "mfa_enabled" for c in d["compliance_checks"])

    ctrl = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert len(ctrl["linked_checks"]) == 1
    assert ctrl["linked_checks"][0]["asset_id"] == aid
    assert ctrl["linked_checks"][0]["effective_status"] == "PASS"


# ---------------------------------------------------------------- DoD #5

def test_validation_and_tenant_isolation(app_client):
    from api.core.database import engine

    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    raw = _longlived_token(app_client, f"agent-{uuid.uuid4().hex[:6]}")
    hh = {"Authorization": f"Bearer {raw}"}

    r = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": aid, "check_key": "x", "check_label": "x", "status": "MAYBE"}]})
    assert r.status_code == 400

    r = app_client.post("/api/integrations/checks", headers=hh, json={"checks": []})
    assert r.status_code == 400

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

    r = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": other_asset, "check_key": "x", "check_label": "x", "status": "PASS"}]})
    assert r.status_code == 404

    r = app_client.post("/api/integrations/checks", headers=hh, json={"checks": [
        {"asset_id": aid, "control_id": "does-not-exist", "check_key": "x",
         "check_label": "x", "status": "PASS"}]})
    assert r.status_code == 404
