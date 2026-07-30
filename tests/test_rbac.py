"""P4-S2 — role-based access control.

The existing suites all passing after the rollout is necessary but NOT sufficient: it would
also happen if nothing were enforced. These tests prove the gate actually bites.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.test_identity import uniq_gst


def _signup(client):
    """A fresh organisation. The signer is its Super Admin (and so bypasses every check)."""
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/signup", json={
        "full_name": "Owner", "email": email, "password": "Passw0rdOne",
        "organisation_name": f"RBAC Org {uuid.uuid4().hex[:6]}", "gst_number": uniq_gst()})
    assert r.status_code == 201, r.text
    return r.json(), email


def _member_with_role(client, tenant_id: str, role_name: str | None):
    """Add a NON-super-admin login to the org, holding `role_name` (or no role at all)."""
    from api.database import engine
    from api.passwords import set_password
    email = f"{(role_name or 'norole').lower()}-{uuid.uuid4().hex[:8]}@example.com"
    with engine.begin() as c:
        uid = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO users (id,email,full_name,auth_provider,"
                          "is_platform_admin,status) VALUES (:i,:e,:n,'local',0,'active')"),
                  {"i": uid, "e": email, "n": role_name or "No Role"})
        rid = c.execute(sqltext("SELECT id FROM roles WHERE tenant_id=:t AND name=:n"),
                        {"t": tenant_id, "n": role_name}).scalar() if role_name else None
        # legacy `role` stays 'member'; role_id is what RBAC reads
        c.execute(sqltext("INSERT INTO tenant_members (id,tenant_id,user_id,role,role_id) "
                          "VALUES (:i,:t,:u,'member',:r)"),
                  {"i": str(uuid.uuid4()), "t": tenant_id, "u": uid, "r": rid})
        set_password(c, uid, "Passw0rdOne")
    tok = client.post("/api/auth/login", json={"email": email, "password": "Passw0rdOne"})
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


# ---------------------------------------------------------------- the gate bites

def test_viewer_can_read_but_not_write(app_client):
    org, _ = _signup(app_client)
    h, _ = _member_with_role(app_client, org["tenant_id"], "Viewer")

    # reads are allowed across the workspace
    for path in ("/api/risks", "/api/assets", "/api/documents", "/api/evidence",
                 "/api/tasks", "/api/dashboard", "/api/library/controls"):
        assert app_client.get(path, headers=h).status_code == 200, path

    # every write is refused
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "nope"}).status_code == 403
    assert app_client.post("/api/assets", headers=h,
                           json={"name": "nope"}).status_code == 403
    assert app_client.post("/api/data-items", headers=h,
                           json={"name": "nope"}).status_code == 403
    assert app_client.post("/api/incidents", headers=h,
                           json={"title": "nope"}).status_code == 403


def test_editor_can_write_but_not_delete(app_client):
    org, _ = _signup(app_client)
    h, _ = _member_with_role(app_client, org["tenant_id"], "Editor")

    made = app_client.post("/api/risks", headers=h, json={"title": "Editor's risk"})
    assert made.status_code == 201, made.text
    rid = made.json()["id"]

    assert app_client.patch(f"/api/risks/{rid}", headers=h,
                            json={"category": "Access"}).status_code == 200
    # …but deleting is an Admin action
    assert app_client.delete(f"/api/risks/{rid}", headers=h).status_code == 403


def test_editor_cannot_touch_users_or_roles(app_client):
    org, _ = _signup(app_client)
    h, _ = _member_with_role(app_client, org["tenant_id"], "Editor")
    assert app_client.get("/api/roles", headers=h).status_code == 403
    assert app_client.post("/api/roles", headers=h,
                           json={"name": "Sneaky", "permissions": []}).status_code == 403


def test_a_role_with_no_permissions_can_do_nothing(app_client):
    """Default deny: an explicit role holding no permissions blocks even reads.

    (A membership with role_id NULL is a different case — it falls back to the legacy role
    map so pre-P4 rows keep working; that path is covered by the suites at large.)"""
    from api.database import engine
    org, _ = _signup(app_client)
    h, uid = _member_with_role(app_client, org["tenant_id"], "Viewer")
    with engine.begin() as c:
        empty = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO roles (id,tenant_id,name,is_system) "
                          "VALUES (:i,:t,'Nothing',0)"), {"i": empty, "t": org["tenant_id"]})
        c.execute(sqltext("UPDATE tenant_members SET role_id=:r WHERE user_id=:u"),
                  {"r": empty, "u": uid})
    assert app_client.get("/api/risks", headers=h).status_code == 403
    assert app_client.get("/api/dashboard", headers=h).status_code == 403


def test_legacy_membership_still_resolves(app_client):
    """Pre-P4 rows have role_id NULL. They must keep working via LEGACY_ROLE_MAP, or every
    fixture and every existing customer would be locked out on upgrade."""
    from api.database import engine
    org, _ = _signup(app_client)
    h, uid = _member_with_role(app_client, org["tenant_id"], "Editor")
    with engine.begin() as c:
        c.execute(sqltext("UPDATE tenant_members SET role_id=NULL, role='member' "
                          "WHERE user_id=:u"), {"u": uid})
    assert app_client.get("/api/risks", headers=h).status_code == 200        # Editor-equivalent
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "legacy"}).status_code == 201
    with engine.begin() as c:
        c.execute(sqltext("UPDATE tenant_members SET role='admin' WHERE user_id=:u"), {"u": uid})
    assert app_client.get("/api/roles", headers=h).status_code == 200        # Admin-equivalent


def test_super_admin_bypasses_every_check(app_client):
    """The org owner can always act in their own organisation, whatever roles say."""
    from api.database import engine
    org, _ = _signup(app_client)
    h = {"Authorization": f"Bearer {org['access_token']}"}
    # strip every permission from every role in the org
    with engine.begin() as c:
        c.execute(sqltext("DELETE FROM role_permissions WHERE role_id IN "
                          "(SELECT id FROM roles WHERE tenant_id=:t)"), {"t": org["tenant_id"]})
    made = app_client.post("/api/risks", headers=h, json={"title": "Owner still works"})
    assert made.status_code == 201, made.text
    assert app_client.delete(f"/api/risks/{made.json()['id']}", headers=h).status_code == 200


def test_permission_changes_take_effect_without_re_login(app_client):
    """Permissions are read from the DB per request — a demotion must bite immediately,
    not when the 12h token happens to expire."""
    from api.database import engine
    org, _ = _signup(app_client)
    h, uid = _member_with_role(app_client, org["tenant_id"], "Editor")

    assert app_client.post("/api/risks", headers=h,
                           json={"title": "before"}).status_code == 201

    viewer = None
    with engine.begin() as c:
        viewer = c.execute(sqltext("SELECT id FROM roles WHERE tenant_id=:t AND name='Viewer'"),
                           {"t": org["tenant_id"]}).scalar()
        c.execute(sqltext("UPDATE tenant_members SET role_id=:r WHERE user_id=:u"),
                  {"r": viewer, "u": uid})

    # same token, no re-login
    assert app_client.post("/api/risks", headers=h,
                           json={"title": "after"}).status_code == 403
    assert app_client.get("/api/risks", headers=h).status_code == 200


def test_permissions_do_not_leak_across_organisations(app_client):
    """An Admin in org A holds nothing in org B."""
    from api.database import engine
    a, _ = _signup(app_client)
    b, _ = _signup(app_client)
    h, uid = _member_with_role(app_client, a["tenant_id"], "Admin")

    # forge a token scoped to org B for this user (they are not a member there)
    from api.auth import create_access_token
    stolen = create_access_token(user_id=uid, tenant_id=b["tenant_id"], role="admin")
    hb = {"Authorization": f"Bearer {stolen}"}
    assert app_client.get("/api/risks", headers=hb).status_code == 403
    assert app_client.post("/api/risks", headers=hb, json={"title": "x"}).status_code == 403


# ---------------------------------------------------------------- /auth/me + roles API

def test_me_reports_the_resolved_permissions(app_client):
    org, _ = _signup(app_client)
    h, _ = _member_with_role(app_client, org["tenant_id"], "Viewer")
    me = app_client.get("/api/auth/me", headers=h).json()
    assert "risks.view" in me["permissions"]
    assert "risks.delete" not in me["permissions"]
    assert me["is_super_admin"] is False

    owner = app_client.get("/api/auth/me",
                           headers={"Authorization": f"Bearer {org['access_token']}"}).json()
    assert owner["is_super_admin"] is True
    assert "risks.delete" in owner["permissions"]


def test_guest_auditor_routes_are_untouched_by_rbac(app_client):
    """The auditor portal uses get_caller, not a member permission — RBAC must not have
    accidentally locked guests out of the endpoints they live on."""
    import inspect
    from api.routers import assessments
    guest_endpoints = ["assessment_detail", "questions_grid", "response_detail",
                       "post_message", "create_finding", "list_findings", "export_answers"]
    for name in guest_endpoints:
        fn = getattr(assessments, name)
        src = inspect.getsource(fn)
        assert "get_caller" in src, f"{name} must stay guest-reachable"
        assert "Depends(require(" not in src.split(")")[0] or "get_caller" in src, name
