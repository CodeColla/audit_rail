"""Phase 10 extension — Enterprise licensing. req/phase10/00-issue-19.md,
docs/phase10/03-enterprise-licensing.md.

Uses its own throwaway RSA keypair, generated fresh per test module — never the real one
`scripts/generate_license_keypair.py` produces for production. These tests have no
dependency on, and no access to, any real signing key.

`settings.deployment_mode` is a plain attribute on the cached singleton
(`api.core.config.settings`) — `monkeypatch.setattr` flips it to `"enterprise"` for the
duration of one test and restores `"public"` automatically at teardown, the same
technique `tests/conftest.py`'s own docstring already names as the way to override a
setting after the module-level `os.environ` window has closed.
"""

import datetime as dt
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text as sqltext

from api.core.config import settings
from api.domain import entitlements, licensing
from tests.conftest import token as login_token
from tests.test_billing_limits import _new_org

ISSUER = licensing.ISSUER


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(encoding=serialization.Encoding.PEM,
                             format=serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=serialization.NoEncryption())
    pub = key.public_key().public_bytes(encoding=serialization.Encoding.PEM,
                                        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub


@pytest.fixture(scope="module")
def keypair(tmp_path_factory):
    priv, pub = _keypair()
    pub_path = tmp_path_factory.mktemp("license_keys") / "public.pem"
    pub_path.write_bytes(pub)
    return priv, pub_path


@pytest.fixture
def enterprise_mode(keypair, monkeypatch):
    """Every test that takes this fixture runs as a standalone Enterprise deployment,
    verifying against THIS module's throwaway public key — restored to Public/no-op by
    monkeypatch at teardown regardless of how the test ends.

    `deployment_license` is a genuine deployment-wide singleton (by design — see its
    comment in db/schema.sql), which is exactly the problem for a test FILE that pretends
    to be several different fresh deployments in turn against one shared, session-scoped
    database: whatever the previous test left in that one row would otherwise leak
    straight into this one's `_new_org()` signup. Starting every test from a clean row is
    what makes each test's own `_set_license_row`/no-row state the only state in play.
    """
    from api.core.database import engine
    _priv, pub_path = keypair
    monkeypatch.setattr(settings, "deployment_mode", "enterprise")
    monkeypatch.setattr(settings, "license_public_key_path", str(pub_path))
    monkeypatch.setattr(settings, "license_grace_days", 30)
    with engine.begin() as c:
        c.execute(sqltext("DELETE FROM deployment_license"))


def _token(priv, **overrides):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {"iss": ISSUER, "customer": "Test Co", "max_members": 10, "storage_gb": 5,
              "iat": now, "exp": now + dt.timedelta(days=30)}
    payload.update(overrides)
    return jwt.encode(payload, priv, algorithm="RS256")


# ──────────────────────────────────────────────────────────────── licensing.decode()

def test_decode_valid_token(keypair, monkeypatch):
    priv, pub_path = keypair
    monkeypatch.setattr(settings, "license_public_key_path", str(pub_path))
    lic = licensing.decode(_token(priv))
    assert lic is not None
    assert lic.customer == "Test Co"
    assert lic.max_members == 10
    assert lic.storage_bytes == 5 * 1024 ** 3
    assert lic.expired is False


def test_decode_rejects_wrong_issuer(keypair, monkeypatch):
    priv, pub_path = keypair
    monkeypatch.setattr(settings, "license_public_key_path", str(pub_path))
    assert licensing.decode(_token(priv, iss="someone-else")) is None


def test_decode_rejects_token_signed_by_a_different_key(keypair, monkeypatch):
    _priv, pub_path = keypair
    other_priv, _other_pub = _keypair()
    monkeypatch.setattr(settings, "license_public_key_path", str(pub_path))
    assert licensing.decode(_token(other_priv)) is None


def test_decode_still_reads_an_expired_but_genuine_token(keypair, monkeypatch):
    priv, pub_path = keypair
    monkeypatch.setattr(settings, "license_public_key_path", str(pub_path))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    lic = licensing.decode(_token(priv, iat=now - dt.timedelta(days=400),
                                  exp=now - dt.timedelta(days=35)))
    assert lic is not None
    assert lic.expired is True
    assert lic.customer == "Test Co"


def test_decode_with_no_public_key_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "license_public_key_path", str(tmp_path / "missing.pem"))
    assert licensing.decode("anything") is None


# ──────────────────────────────────────────────────────────── entitlements state machine

def _set_license_row(engine, token, applied_days_ago=0):
    from api.core.util import now_iso
    applied_at = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(days=applied_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with engine.begin() as c:
        c.execute(sqltext("DELETE FROM deployment_license WHERE id=1"))
        c.execute(sqltext(
            "INSERT INTO deployment_license (id,token,applied_at) VALUES (1,:t,:a)"),
            {"t": token, "a": applied_at})


def _backdate_tenant(engine, tenant_id, days_ago):
    created = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with engine.begin() as c:
        c.execute(sqltext("UPDATE tenants SET created_at=:c WHERE id=:t"),
                 {"c": created, "t": tenant_id})


def test_never_licensed_is_unlimited_within_onboarding_grace(app_client, enterprise_mode):
    from api.core.database import engine
    j, h = _new_org(app_client, f"ent1-{uuid.uuid4().hex[:8]}")
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.max_members is None
    assert limits.storage_bytes is None
    assert limits.max_active_audits is None
    assert limits.license_issue is None


def test_never_licensed_freezes_past_onboarding_grace(app_client, enterprise_mode):
    from api.core.database import engine
    j, h = _new_org(app_client, f"ent2-{uuid.uuid4().hex[:8]}")
    _backdate_tenant(engine, j["tenant_id"], days_ago=31)
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.degraded is True
    assert limits.license_issue == "missing"
    assert limits.max_active_audits is None   # audits are NEVER capped, even frozen
    assert limits.max_members == 1            # pinned to current usage: just the founder


def test_valid_unexpired_license_gives_its_own_numbers(app_client, enterprise_mode, keypair):
    from api.core.database import engine
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent3-{uuid.uuid4().hex[:8]}")
    _set_license_row(engine, _token(priv, max_members=42, storage_gb=7))
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.max_members == 42
    assert limits.storage_bytes == 7 * 1024 ** 3
    assert limits.max_active_audits is None
    assert limits.degraded is False
    assert limits.in_grace is False


def test_expired_license_within_grace_keeps_its_numbers(app_client, enterprise_mode, keypair):
    from api.core.database import engine
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent4-{uuid.uuid4().hex[:8]}")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    expired = _token(priv, max_members=42, storage_gb=7,
                     iat=now - dt.timedelta(days=100), exp=now - dt.timedelta(days=5))
    _set_license_row(engine, expired)
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.in_grace is True
    assert limits.max_members == 42          # continuity — no disruption from a late renewal
    assert limits.degraded is False


def test_expired_license_past_grace_freezes_at_current_usage(app_client, enterprise_mode, keypair):
    from api.core.database import engine
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent5-{uuid.uuid4().hex[:8]}")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    long_expired = _token(priv, max_members=42, storage_gb=7,
                          iat=now - dt.timedelta(days=400), exp=now - dt.timedelta(days=31))
    _set_license_row(engine, long_expired)
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.degraded is True
    assert limits.license_issue == "expired"
    assert limits.max_members == 1            # frozen at current usage, NOT the stale 42
    assert limits.max_active_audits is None


def test_unverifiable_token_within_its_own_grace_is_unlimited(app_client, enterprise_mode):
    from api.core.database import engine
    j, h = _new_org(app_client, f"ent6-{uuid.uuid4().hex[:8]}")
    _set_license_row(engine, "not-even-a-jwt", applied_days_ago=1)
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.license_issue is None       # within grace: no disruption yet
    assert limits.max_members is None


def test_unverifiable_token_past_its_own_grace_freezes(app_client, enterprise_mode):
    from api.core.database import engine
    j, h = _new_org(app_client, f"ent7-{uuid.uuid4().hex[:8]}")
    _set_license_row(engine, "not-even-a-jwt", applied_days_ago=31)
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, j["tenant_id"])
    assert limits.license_issue == "invalid"
    assert limits.degraded is True


# ──────────────────────────────────────────────────────────────────── HTTP surface

def test_apply_license_rejects_bad_signature_and_writes_nothing(app_client, enterprise_mode):
    from api.core.database import engine
    _priv, _pub = _keypair()  # a key NOT configured as the deployment's public key
    j, h = _new_org(app_client, f"ent8-{uuid.uuid4().hex[:8]}")
    r = app_client.put("/api/license", headers=h, json={"token": _token(_priv)})
    assert r.status_code == 400, r.text
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM deployment_license")).scalar() == 0


def test_apply_license_accepts_a_valid_token_even_if_already_expired(
        app_client, enterprise_mode, keypair):
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent9-{uuid.uuid4().hex[:8]}")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    expired = _token(priv, customer="Already Expired Co",
                     iat=now - dt.timedelta(days=100), exp=now - dt.timedelta(days=1))
    r = app_client.put("/api/license", headers=h, json={"token": expired})
    assert r.status_code == 201, r.text
    assert r.json()["customer"] == "Already Expired Co"


def test_apply_license_400s_on_a_public_deployment(app_client):
    h = {"Authorization": f"Bearer {login_token(app_client, 'admin@kiam.example', 'secret1')}"}
    r = app_client.put("/api/license", headers=h, json={"token": "whatever"})
    assert r.status_code == 400, r.text


def test_billing_reports_license_shape_in_enterprise_mode(app_client, enterprise_mode, keypair):
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent10-{uuid.uuid4().hex[:8]}")
    app_client.put("/api/license", headers=h,
                   json={"token": _token(priv, customer="Shape Co")})
    r = app_client.get("/api/billing", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deployment_mode"] == "enterprise"
    assert body["license"]["customer"] == "Shape Co"
    assert body["license"]["status"] == "active"
    assert "billing_cycle" not in body


def test_billing_reports_public_shape_by_default(app_client):
    h = {"Authorization": f"Bearer {login_token(app_client, 'admin@kiam.example', 'secret1')}"}
    r = app_client.get("/api/billing", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deployment_mode"] == "public"
    assert "license" not in body


def test_member_cap_from_license_is_enforced_end_to_end(app_client, enterprise_mode, keypair):
    """Proves the whole stack is actually wired together, not just effective_limits() in
    isolation: a tight license applied through the real endpoint blocks a real invite."""
    priv, _pub = keypair
    j, h = _new_org(app_client, f"ent11-{uuid.uuid4().hex[:8]}")
    app_client.put("/api/license", headers=h,
                   json={"token": _token(priv, max_members=1)})  # founder already fills it

    r = app_client.post("/api/people", headers=h,
                        json={"full_name": "Second Login", "email": "second@example.com"})
    pid = r.json()["id"]
    r = app_client.post(f"/api/people/{pid}/invite", headers=h, json={})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "plan_limit"
    assert detail["limit"] == "members"
