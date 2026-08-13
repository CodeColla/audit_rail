"""M1 auth & RBAC behavior."""

from tests.conftest import token


def test_login_success_returns_token_and_role(app_client):
    r = app_client.post("/api/auth/login",
                        json={"email": "admin@kiam.example", "password": "secret1"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"
    assert body["access_token"]


def test_login_wrong_password_rejected(app_client):
    r = app_client.post("/api/auth/login",
                        json={"email": "admin@kiam.example", "password": "nope"})
    assert r.status_code == 401


def test_login_unknown_user_rejected(app_client):
    r = app_client.post("/api/auth/login",
                        json={"email": "ghost@kiam.example", "password": "secret1"})
    assert r.status_code == 401


def test_protected_route_requires_token(app_client):
    assert app_client.get("/api/library/domains").status_code == 401


def test_protected_route_with_token_ok(app_client):
    tok = token(app_client, "member@kiam.example", "secret2")
    r = app_client.get("/api/library/domains", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert any(d["code"] == "AM" for d in r.json())


def test_bad_token_rejected(app_client):
    r = app_client.get("/api/library/domains",
                       headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_me_reflects_principal(app_client):
    tok = token(app_client, "member@kiam.example", "secret2")
    r = app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "member@kiam.example"
    assert body["role"] == "member"
    assert body["kind"] == "member"


def test_login_writes_activity_log(app_client):
    from sqlalchemy import select
    from api.core.database import engine, t
    token(app_client, "admin@kiam.example", "secret1")
    with engine.connect() as conn:
        rows = conn.execute(
            select(t("activity_log")).where(t("activity_log").c.action == "auth.login")
        ).mappings().all()
    assert len(rows) >= 1
