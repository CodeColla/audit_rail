"""P4-S1 — organisations, signup, password policy, invites, org switching."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import text as sqltext

from api.gstin import checksum
from tests.conftest import token


def gst(base="27AAPFU0939F1Z"):
    """A structurally valid GSTIN. The 15th char is the real check digit."""
    return base + checksum(base)


def uniq_gst():
    """A distinct but still checksum-valid GSTIN per test (PAN digits vary)."""
    n = uuid.uuid4().int % 10000
    base = f"27AAPFU{n:04d}F1Z"
    return base + checksum(base)


def _signup(client, **over):
    body = {"full_name": "New Owner", "email": f"owner-{uuid.uuid4().hex[:8]}@example.com",
            "password": "Passw0rdOne", "organisation_name": f"Org {uuid.uuid4().hex[:6]}",
            "gst_number": uniq_gst()}
    body.update(over)
    return client.post("/api/auth/signup", json=body), body


# ---------------------------------------------------------------- signup + org

def test_signup_creates_account_and_organisation(app_client):
    from api.database import engine
    r, body = _signup(app_client)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["access_token"] and out["role"] == "admin"
    assert [o["name"] for o in out["organisations"]] == [body["organisation_name"]]

    with engine.connect() as c:
        row = c.execute(sqltext("SELECT name, gst_number, super_admin_user_id FROM tenants "
                                "WHERE id=:t"), {"t": out["tenant_id"]}).mappings().first()
    assert row["gst_number"] == body["gst_number"]
    assert row["super_admin_user_id"], "signer should be the org's Super Admin"

    # and they can immediately sign in
    assert app_client.post("/api/auth/login", json={
        "email": body["email"], "password": body["password"]}).status_code == 200


def test_gst_number_must_be_valid_and_unique(app_client):
    # a well-formed number with a wrong check digit is refused
    bad = app_client.post("/api/auth/signup", json={
        "full_name": "X", "email": f"x-{uuid.uuid4().hex[:6]}@example.com",
        "password": "Passw0rdOne", "organisation_name": "Bad GST Co",
        "gst_number": "27AAPFU0939F1ZZ"})
    assert bad.status_code == 400 and "check digit" in bad.json()["detail"]

    # the same GSTIN cannot create a second organisation
    shared = uniq_gst()
    first, _ = _signup(app_client, gst_number=shared)
    assert first.status_code == 201
    dupe, _ = _signup(app_client, gst_number=shared)
    assert dupe.status_code == 409 and "already exists" in dupe.json()["detail"]


def test_signup_rejects_weak_passwords_and_duplicate_email(app_client):
    for pw, why in [("short1", "at least"), ("alllettersnodigits", "letters and numbers")]:
        r, _ = _signup(app_client, password=pw)
        assert r.status_code == 400, pw
        assert why in r.json()["detail"]

    r, body = _signup(app_client)
    assert r.status_code == 201
    again, _ = _signup(app_client, email=body["email"])
    assert again.status_code == 409 and "sign in instead" in again.json()["detail"]


# ---------------------------------------------------------------- password policy

def test_previous_three_passwords_cannot_be_reused(app_client):
    r, body = _signup(app_client)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    chain = ["Passw0rdOne", "Passw0rdTwo", "Passw0rdThree", "Passw0rdFour"]

    for prev, nxt in zip(chain, chain[1:]):
        assert app_client.post("/api/auth/change-password", headers=h, json={
            "current_password": prev, "new_password": nxt}).status_code == 200, nxt

    # now at Four; Three and Two are still retained, One has aged out
    for reused in ("Passw0rdThree", "Passw0rdTwo"):
        bad = app_client.post("/api/auth/change-password", headers=h, json={
            "current_password": "Passw0rdFour", "new_password": reused})
        assert bad.status_code == 400, reused
        assert "last 3 passwords" in bad.json()["detail"]

    ok = app_client.post("/api/auth/change-password", headers=h, json={
        "current_password": "Passw0rdFour", "new_password": "Passw0rdOne"})
    assert ok.status_code == 200, "the oldest password should have fallen out of history"


def test_wrong_current_password_is_rejected(app_client):
    r, _ = _signup(app_client)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    bad = app_client.post("/api/auth/change-password", headers=h, json={
        "current_password": "NotMyPassw0rd", "new_password": "Brandnew123"})
    assert bad.status_code == 400 and "not correct" in bad.json()["detail"]


def test_password_expires_after_30_days(app_client):
    from api.database import engine
    r, body = _signup(app_client)
    tok = r.json()
    assert tok["must_change_password"] is False
    assert tok["password_expires_in_days"] == 30

    # age the current password past the policy window
    old = (dt.date.today() - dt.timedelta(days=31)).isoformat() + "T00:00:00Z"
    with engine.begin() as c:
        uid = c.execute(sqltext("SELECT id FROM users WHERE email=:e"),
                        {"e": body["email"]}).scalar()
        c.execute(sqltext("UPDATE user_password_history SET changed_at=:d "
                          "WHERE user_id=:u AND level=0"), {"d": old, "u": uid})

    again = app_client.post("/api/auth/login", json={
        "email": body["email"], "password": body["password"]})
    assert again.status_code == 200
    assert again.json()["must_change_password"] is True
    assert again.json()["password_expires_in_days"] < 0


# ---------------------------------------------------------------- multi-org

def test_super_admin_can_run_several_organisations_and_switch(app_client):
    r, body = _signup(app_client)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    first_tid = r.json()["tenant_id"]

    second = app_client.post("/api/auth/orgs", headers=h,
                             json={"name": "Second Org", "gst_number": uniq_gst()})
    assert second.status_code == 201, second.text
    second_tid = second.json()["tenant_id"]

    me = app_client.get("/api/auth/me", headers=h).json()
    assert me["is_super_admin"] is True
    assert {o["tenant_id"] for o in me["organisations"]} == {first_tid, second_tid}

    sw = app_client.post("/api/auth/switch-org", headers=h, json={"tenant_id": second_tid})
    assert sw.status_code == 200 and sw.json()["tenant_id"] == second_tid

    # the new token really is scoped to the other org
    h2 = {"Authorization": f"Bearer {sw.json()['access_token']}"}
    assert app_client.get("/api/auth/me", headers=h2).json()["tenant_id"] == second_tid


def test_cannot_switch_into_an_organisation_you_do_not_belong_to(app_client):
    a, _ = _signup(app_client)
    b, _ = _signup(app_client)
    ha = {"Authorization": f"Bearer {a.json()['access_token']}"}
    r = app_client.post("/api/auth/switch-org", headers=ha,
                        json={"tenant_id": b.json()["tenant_id"]})
    assert r.status_code == 403


def test_login_picks_a_deterministic_org_not_an_arbitrary_one(app_client):
    """`authenticate()` used to do `.limit(1)` with no ORDER BY, so a multi-org user could
    land in a different organisation between logins."""
    r, body = _signup(app_client)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    app_client.post("/api/auth/orgs", headers=h,
                    json={"name": "Another", "gst_number": uniq_gst()})
    seen = {app_client.post("/api/auth/login", json={
        "email": body["email"], "password": body["password"]}).json()["tenant_id"]
        for _ in range(5)}
    assert len(seen) == 1, f"login landed in {len(seen)} different orgs: {seen}"

    # …and can still be asked for a specific one
    target = r.json()["tenant_id"]
    picked = app_client.post("/api/auth/login", json={
        "email": body["email"], "password": body["password"], "tenant_id": target})
    assert picked.json()["tenant_id"] == target


# ---------------------------------------------------------------- invites

def test_invite_lets_a_user_set_their_own_password(app_client):
    from api.database import engine
    from api.routers.auth import issue_invite
    r, _ = _signup(app_client)
    tid = r.json()["tenant_id"]

    email = f"invitee-{uuid.uuid4().hex[:6]}@example.com"
    with engine.begin() as c:
        uid = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO users (id,email,full_name,auth_provider,"
                          "is_platform_admin,status) VALUES (:i,:e,'Invitee','local',0,"
                          "'invited')"), {"i": uid, "e": email})
        c.execute(sqltext("INSERT INTO tenant_members (id,tenant_id,user_id,role) "
                          "VALUES (:i,:t,:u,'member')"),
                  {"i": str(uuid.uuid4()), "t": tid, "u": uid})
        raw = issue_invite(c, tenant_id=tid, user_id=uid, invited_by=None)

    # an invited user has no password yet, so cannot log in
    assert app_client.post("/api/auth/login", json={
        "email": email, "password": "anything123"}).status_code == 401

    accept = app_client.post("/api/auth/accept-invite",
                             json={"token": raw, "password": "MyOwnPass1"})
    assert accept.status_code == 200, accept.text
    assert accept.json()["tenant_id"] == tid

    assert app_client.post("/api/auth/login", json={
        "email": email, "password": "MyOwnPass1"}).status_code == 200
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT status FROM users WHERE id=:u"),
                         {"u": uid}).scalar() == "active"


def test_invite_token_is_single_use_and_hash_only(app_client):
    from api.database import engine
    from api.routers.auth import issue_invite
    r, _ = _signup(app_client)
    tid = r.json()["tenant_id"]
    with engine.begin() as c:
        uid = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO users (id,email,full_name,auth_provider,"
                          "is_platform_admin,status) VALUES (:i,:e,'Once','local',0,"
                          "'invited')"), {"i": uid, "e": f"once-{uuid.uuid4().hex[:6]}@ex.com"})
        c.execute(sqltext("INSERT INTO tenant_members (id,tenant_id,user_id,role) "
                          "VALUES (:i,:t,:u,'member')"),
                  {"i": str(uuid.uuid4()), "t": tid, "u": uid})
        raw = issue_invite(c, tenant_id=tid, user_id=uid, invited_by=None)

    # the raw token is nowhere in the table — only its sha256
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM user_invites WHERE token_hash=:r"),
                         {"r": raw}).scalar() == 0

    assert app_client.post("/api/auth/accept-invite",
                           json={"token": raw, "password": "FirstUse11"}).status_code == 200
    again = app_client.post("/api/auth/accept-invite",
                            json={"token": raw, "password": "SecondUse2"})
    assert again.status_code == 410


def test_only_a_super_admin_can_create_an_organisation(app_client):
    """The seeded member owns no organisation, so they cannot create one."""
    h = {"Authorization": f"Bearer {token(app_client, 'member@kiam.example', 'secret2')}"}
    r = app_client.post("/api/auth/orgs", headers=h,
                        json={"name": "Sneaky Org", "gst_number": uniq_gst()})
    assert r.status_code == 403


def test_auth_endpoints_still_require_a_token_where_they_should(app_client):
    assert app_client.post("/api/auth/orgs",
                           json={"name": "x", "gst_number": gst()}).status_code == 401
    assert app_client.post("/api/auth/change-password", json={
        "current_password": "a", "new_password": "b"}).status_code == 401
