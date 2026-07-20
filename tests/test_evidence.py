"""M3 — evidence upload / status / download / linking / delete RBAC."""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _a_control(engine, tenant_id):
    cid = str(uuid.uuid4())
    with engine.begin() as c:
        dom = c.execute(sqltext("SELECT id FROM domains WHERE code='AM' AND tenant_id=:t"),
                        {"t": tenant_id}).scalar()
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "recurrence_months,applicability,status,created_at,updated_at) VALUES "
            "(:i,:t,:d,'EVT 1.a','Password policy','recurring',12,'applicable','active',"
            "'2026-07-11T00:00:00Z','2026-07-11T00:00:00Z')"),
            {"i": cid, "t": tenant_id, "d": dom})
    return cid


def _upload(client, tok, title, valid_until, control_ids=""):
    return client.post(
        "/api/evidence",
        headers={"Authorization": f"Bearer {tok}"},
        data={"title": title, "evidence_type": "report",
              "issued_at": "2025-01-01", "valid_until": valid_until,
              "control_ids": control_ids},
        files={"file": (f"{title}.pdf", b"%PDF-1.4 fake evidence bytes", "application/pdf")},
    )


def test_upload_status_download_link_delete(app_client):
    from api.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants LIMIT 1")).scalar()
    ctl = _a_control(engine, tid)
    tok_member = token(app_client, "member@kiam.example", "secret2")
    h = {"Authorization": f"Bearer {tok_member}"}

    # expired + valid uploads
    r1 = _upload(app_client, tok_member, "Old VAPT", "2020-01-01", control_ids=ctl)
    assert r1.status_code == 201, r1.text
    assert r1.json()["linked_controls"] == 1
    assert len(r1.json()["sha256"]) == 64
    ev_id = r1.json()["id"]
    r2 = _upload(app_client, tok_member, "Fresh Cert", "2099-01-01")
    assert r2.status_code == 201

    # list with derived status
    items = app_client.get("/api/evidence", headers=h).json()
    by_title = {e["title"]: e for e in items}
    assert by_title["Old VAPT"]["status"] == "expired"
    assert by_title["Fresh Cert"]["status"] == "valid"

    # expiring filter shows only the expired one
    exp = app_client.get("/api/evidence?expiring=true", headers=h).json()
    assert {e["title"] for e in exp} == {"Old VAPT"}

    # detail carries the linked control
    detail = app_client.get(f"/api/evidence/{ev_id}", headers=h).json()
    assert detail["linked_controls"][0]["code"] == "EVT 1.a"

    # download returns the exact bytes
    dl = app_client.get(f"/api/evidence/{ev_id}/file", headers=h)
    assert dl.status_code == 200
    assert dl.content == b"%PDF-1.4 fake evidence bytes"

    # member cannot delete (403); admin can (204)
    assert app_client.delete(f"/api/evidence/{ev_id}", headers=h).status_code == 403
    tok_admin = token(app_client, "admin@kiam.example", "secret1")
    r = app_client.delete(f"/api/evidence/{ev_id}",
                          headers={"Authorization": f"Bearer {tok_admin}"})
    assert r.status_code == 204
    assert app_client.get(f"/api/evidence/{ev_id}", headers=h).status_code == 404


def test_evidence_requires_auth(app_client):
    assert app_client.get("/api/evidence").status_code == 401
