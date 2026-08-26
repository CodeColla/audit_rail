"""P8-S2 — alert -> finding ingestion.

`ingested_alerts` is a STAGING table, not `findings` — confirmed while building this that
`findings` are only ever created inside an assessment and only ever listed by joining through
`finding_assessments` for one specific assessment, so a standalone-ingested alert has nowhere
in the existing UI to surface if written there directly. A human reviews here and promotes to
a real finding by hand if it's audit-worthy.

DoD, executable:
  1. an alert ingests, defaults occurred_at, and is visible via the JWT-authed list.
  2. re-delivery with the same source_event_id dedupes instead of duplicating.
  3. an asset_id must belong to the token's tenant; a bad severity is a 400, not a 500.
  4. a human can mark an alert reviewed/dismissed; status is tenant-isolated.
  5. an alert with no asset_id (asset_id null) still ingests and lists fine.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _longlived_token(app_client, name):
    h = _h(app_client)
    raw_install = app_client.post("/api/integrations/tokens/install", headers=h,
                                  json={"name": name}).json()["token"]
    return app_client.post("/api/integrations/tokens/exchange",
                           json={"install_token": raw_install}).json()["token"]


# ---------------------------------------------------------------- DoD #1

def test_alert_ingests_and_is_listed(app_client):
    h = _h(app_client)
    aid = app_client.post("/api/assets", headers=h,
                          json={"name": f"Server {uuid.uuid4().hex[:6]}"}).json()["id"]
    raw = _longlived_token(app_client, f"siem-{uuid.uuid4().hex[:6]}")

    r = app_client.post("/api/integrations/alerts",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"asset_id": aid, "title": "Unusual login volume",
                             "severity": "HIGH"})
    assert r.status_code == 201, r.text
    alert_id = r.json()["id"]
    assert r.json()["deduplicated"] is False

    rows = app_client.get("/api/integrations/alerts", headers=h).json()
    mine = next(x for x in rows if x["id"] == alert_id)
    assert mine["title"] == "Unusual login volume"
    assert mine["status"] == "new"
    assert mine["occurred_at"] is not None  # defaulted server-side


# ---------------------------------------------------------------- DoD #2

def test_redelivery_dedupes_on_source_event_id(app_client):
    h = _h(app_client)
    raw = _longlived_token(app_client, f"siem-{uuid.uuid4().hex[:6]}")
    ev_id = f"evt-{uuid.uuid4().hex[:8]}"

    body = {"title": "Firewall rule changed", "source_event_id": ev_id}
    r1 = app_client.post("/api/integrations/alerts",
                         headers={"Authorization": f"Bearer {raw}"}, json=body)
    r2 = app_client.post("/api/integrations/alerts",
                         headers={"Authorization": f"Bearer {raw}"}, json=body)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["deduplicated"] is True

    # a THIRD alert with no source_event_id at all must never collide with either of the above
    r3 = app_client.post("/api/integrations/alerts",
                         headers={"Authorization": f"Bearer {raw}"},
                         json={"title": "No dedup key here"})
    assert r3.status_code == 201
    assert r3.json()["id"] not in (r1.json()["id"],)


# ---------------------------------------------------------------- DoD #3

def test_bad_asset_and_bad_severity_are_400_or_404_not_500(app_client):
    from api.core.database import engine

    h = _h(app_client)
    raw = _longlived_token(app_client, f"siem-{uuid.uuid4().hex[:6]}")

    r = app_client.post("/api/integrations/alerts",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"title": "x", "severity": "APOCALYPTIC"})
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

    r = app_client.post("/api/integrations/alerts",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"title": "x", "asset_id": other_asset})
    assert r.status_code == 404


# ---------------------------------------------------------------- DoD #4

def test_review_and_dismiss_are_tenant_isolated(app_client):
    h = _h(app_client)
    raw = _longlived_token(app_client, f"siem-{uuid.uuid4().hex[:6]}")
    aid = app_client.post("/api/integrations/alerts",
                          headers={"Authorization": f"Bearer {raw}"},
                          json={"title": "Suspicious process"}).json()["id"]

    bad = app_client.patch(f"/api/integrations/alerts/{aid}", headers=h,
                           json={"status": "closed"})
    assert bad.status_code == 400

    r = app_client.patch(f"/api/integrations/alerts/{aid}", headers=h,
                         json={"status": "reviewed"})
    assert r.status_code == 200, r.text
    rows = app_client.get("/api/integrations/alerts", headers=h,
                          params={"status": "reviewed"}).json()
    assert any(x["id"] == aid for x in rows)

    r = app_client.patch("/api/integrations/alerts/does-not-exist", headers=h,
                         json={"status": "dismissed"})
    assert r.status_code == 404


# ---------------------------------------------------------------- DoD #5

def test_unlinked_alert_ingests_fine(app_client):
    h = _h(app_client)
    raw = _longlived_token(app_client, f"siem-{uuid.uuid4().hex[:6]}")
    r = app_client.post("/api/integrations/alerts",
                        headers={"Authorization": f"Bearer {raw}"},
                        json={"title": "Alert with no matching asset"})
    assert r.status_code == 201, r.text
    rows = app_client.get("/api/integrations/alerts", headers=h).json()
    mine = next(x for x in rows if x["id"] == r.json()["id"])
    assert mine["asset_id"] is None
