"""Phase 10 (S2a) — plan limits. req/phase10/00-issue-19.md, docs/phase10/.

A fresh signup starts on Free (1 active audit, 500 MB, 3 members); the KIAM fixture is
grandfathered to Enterprise (unlimited) so none of the ~470 pre-existing tests are
affected — see tests/conftest.py. Tests that need a Free org create their own via
`_new_org`, the same pattern tests/test_branding.py already uses.

Not covered here: the generated-document PDF (`documents._render_and_store`) is never
storage-checked — that's a STRUCTURAL guarantee (no call site, see
api/routers/documents.py), not a race that a runtime test would catch, so there is no
test for it. Razorpay lifecycle (activated/past_due/cancelled → effective_limits) IS
covered, directly against `entitlements.effective_limits`, without a gateway — those
state transitions don't need one.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token

#: A real one-pixel PNG — same fixture as tests/test_branding.py, for the same reason: the
#: upload sniffer checks magic bytes, not the filename.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63e095d20600008a005328b499f20000000049454e44"
    "ae426082")


def _new_org(client, tag):
    r = client.post("/api/auth/signup", json={
        "full_name": f"{tag} owner", "email": f"{tag}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Org {tag} Pvt Ltd"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j, {"Authorization": f"Bearer {j['access_token']}"}


def _make_template(engine, tenant_id, tag):
    tpl_id, now = str(uuid.uuid4()), "2026-10-02T00:00:00Z"
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO templates (id,tenant_id,bank_name,title,status,created_at) "
            "VALUES (:i,:t,:b,:x,'active',:n)"),
            {"i": tpl_id, "t": tenant_id, "b": f"Bank {tag}", "x": f"Checklist {tag}", "n": now})
    return tpl_id


def test_second_active_audit_blocked_on_free(app_client):
    from api.core.database import engine

    _, h = _new_org(app_client, f"audit1-{uuid.uuid4().hex[:8]}")
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    tpl = _make_template(engine, tid, "a")

    r1 = app_client.post("/api/assessments", headers=h,
                         json={"title": "Audit A", "template_id": tpl})
    assert r1.status_code == 201, r1.text

    r2 = app_client.post("/api/assessments", headers=h,
                         json={"title": "Audit B", "template_id": tpl})
    assert r2.status_code == 402, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "plan_limit"
    assert detail["limit"] == "audits"
    assert detail["max"] == 1


def test_reopening_closed_audit_at_cap_is_blocked(app_client):
    """A closed audit can be reopened through PATCH — reopening must check the same cap
    as creating, or the audit limit is dodged by closing and reopening (the loophole named
    in req/phase10/00-issue-19.md §1)."""
    from api.core.database import engine

    _, h = _new_org(app_client, f"audit2-{uuid.uuid4().hex[:8]}")
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    tpl = _make_template(engine, tid, "b")

    a = app_client.post("/api/assessments", headers=h,
                        json={"title": "Audit A", "template_id": tpl}).json()["id"]
    app_client.patch(f"/api/assessments/{a}", headers=h, json={"status": "closed"})

    # the slot A left is immediately free — reopening it alone must succeed
    r_reopen_alone = app_client.patch(f"/api/assessments/{a}", headers=h,
                                      json={"status": "draft"})
    assert r_reopen_alone.status_code == 200, r_reopen_alone.text
    app_client.patch(f"/api/assessments/{a}", headers=h, json={"status": "closed"})

    # now a DIFFERENT audit occupies the one Free slot...
    b = app_client.post("/api/assessments", headers=h,
                        json={"title": "Audit B", "template_id": tpl}).json()["id"]

    # ...so reopening A must be blocked exactly like creating a third audit would be
    r = app_client.patch(f"/api/assessments/{a}", headers=h, json={"status": "draft"})
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["limit"] == "audits"


def test_template_import_blocked_at_cap(app_client):
    """A new audit is always created straight after a checklist import — checking only at
    creation would leave a Free org at its cap with an orphan checklist and no audit."""
    from api.core.database import engine

    _, h = _new_org(app_client, f"audit3-{uuid.uuid4().hex[:8]}")
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    tpl = _make_template(engine, tid, "c")
    app_client.post("/api/assessments", headers=h,
                    json={"title": "Audit A", "template_id": tpl})

    with engine.connect() as c:
        templates_before = c.execute(sqltext(
            "SELECT count(*) FROM templates WHERE tenant_id=:t"), {"t": tid}).scalar()

    import json as _json
    rows = _json.dumps([{"number": "1", "section": "General", "text": "Do you have an ISP?"}])
    r = app_client.post("/api/templates/import", headers=h,
                        data={"bank_name": "New Bank", "rows": rows},
                        files={"file": ("checklist.xlsx", b"not a real workbook",
                                        "application/vnd.openxmlformats")})
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["limit"] == "audits"

    with engine.connect() as c:
        templates_after = c.execute(sqltext(
            "SELECT count(*) FROM templates WHERE tenant_id=:t"), {"t": tid}).scalar()
    assert templates_after == templates_before, "a blocked import must not write anything"


def test_storage_limit_blocked(app_client):
    from api.core.database import engine

    _, h = _new_org(app_client, f"store1-{uuid.uuid4().hex[:8]}")
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    # the real 500 MB cap is impractical to hit in a test — shrink it with an override
    # instead, same mechanism scripts/set_plan.py uses for a real Enterprise deal.
    with engine.begin() as c:
        c.execute(sqltext(
            "UPDATE tenant_plans SET override_storage_bytes=10 WHERE tenant_id=:t"),
            {"t": tid})

    r = app_client.post("/api/evidence", headers=h,
                        data={"evidence_type": "document"},
                        files={"file": ("big.png", PNG, "image/png")})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "plan_limit"
    assert detail["limit"] == "storage"


def test_fourth_login_blocked_people_without_login_unaffected(app_client):
    _, h = _new_org(app_client, f"mem1-{uuid.uuid4().hex[:8]}")

    def add_person(n):
        r = app_client.post("/api/people", headers=h,
                            json={"full_name": f"Person {n}", "email": f"p{n}@example.com"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    # people records with NO login are never counted — Free allows 3 LOGINS (the founding
    # admin is already one), and this adds five people records with none.
    for n in range(5):
        add_person(n)

    # 2 invited logins, on top of the founding admin, reaches the cap of 3
    for n in range(2):
        pid = add_person(f"login{n}")
        r = app_client.post(f"/api/people/{pid}/invite", headers=h, json={})
        assert r.status_code == 201, r.text

    pid = add_person("login-over")
    r = app_client.post(f"/api/people/{pid}/invite", headers=h, json={})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "plan_limit"
    assert detail["limit"] == "members"
    assert detail["max"] == 3


def test_guest_auditors_are_never_counted(app_client):
    from api.core.database import engine

    _, h = _new_org(app_client, f"guest1-{uuid.uuid4().hex[:8]}")
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    tpl = _make_template(engine, tid, "d")
    aid = app_client.post("/api/assessments", headers=h,
                          json={"title": "Audit A", "template_id": tpl}).json()["id"]

    from api.domain import entitlements
    with engine.connect() as conn:
        before = entitlements.usage(conn, tid).members

    for n in range(4):
        r = app_client.post(f"/api/assessments/{aid}/guests", headers=h,
                            json={"email": f"auditor{n}@bank.example", "full_name": f"Auditor {n}"})
        assert r.status_code == 201, r.text

    with engine.connect() as conn:
        after = entitlements.usage(conn, tid).members
    assert after == before, "guest auditors must never count toward the member limit"


def test_storage_usage_is_per_tenant_only(app_client):
    from api.core.database import engine
    from api.domain import entitlements

    _, h_a = _new_org(app_client, f"iso-a-{uuid.uuid4().hex[:8]}")
    _, h_b = _new_org(app_client, f"iso-b-{uuid.uuid4().hex[:8]}")
    tid_b = app_client.get("/api/auth/me", headers=h_b).json()["tenant_id"]

    r = app_client.post("/api/evidence", headers=h_a,
                        data={"evidence_type": "document"},
                        files={"file": ("a.png", PNG, "image/png")})
    assert r.status_code == 201, r.text

    with engine.connect() as conn:
        assert entitlements.usage(conn, tid_b).storage_bytes == 0


def test_missing_tenant_plans_row_is_unlimited(app_client, caplog):
    from api.core.database import engine
    from api.domain import entitlements
    from sqlalchemy import text as sqltext2

    tid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext2(
            "INSERT INTO tenants (id,name,slug,status,created_at) "
            "VALUES (:i,'No Plan Org','no-plan-org','active','2026-10-02T00:00:00Z')"),
            {"i": tid})
        # deliberately NO tenant_plans row

    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, tid)
    assert limits.plan_code == "enterprise"
    assert limits.max_active_audits is None
    assert limits.storage_bytes is None
    assert limits.max_members is None


def test_past_due_grace_then_lapse_to_free(app_client):
    from api.core.database import engine
    from api.domain import entitlements

    tid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO tenants (id,name,slug,status,created_at) "
            "VALUES (:i,'Grace Org','grace-org','active','2026-10-02T00:00:00Z')"), {"i": tid})
        c.execute(sqltext(
            "INSERT INTO tenant_plans (tenant_id,plan_code,status,grace_until) "
            "VALUES (:t,'pro','past_due','2099-01-01T00:00:00Z')"), {"t": tid})

    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, tid)
    assert limits.plan_code == "pro"
    assert limits.in_grace is True
    assert limits.max_active_audits == 10  # still full Pro limits inside grace

    with engine.begin() as c:
        c.execute(sqltext(
            "UPDATE tenant_plans SET grace_until='2000-01-01T00:00:00Z' WHERE tenant_id=:t"),
            {"t": tid})
    with engine.connect() as conn:
        limits = entitlements.effective_limits(conn, tid)
    assert limits.plan_code == "free"
    assert limits.degraded is True
    assert limits.max_active_audits == 1  # dropped to Free limits once grace ran out


def test_signup_and_second_org_both_get_a_free_plan_row(app_client):
    from api.core.database import engine

    j, h = _new_org(app_client, f"planrow-{uuid.uuid4().hex[:8]}")
    tid1 = j["tenant_id"]

    r = app_client.post("/api/auth/orgs", headers=h, json={"name": "Second Org"})
    assert r.status_code == 201, r.text
    tid2 = r.json()["tenant_id"]

    with engine.connect() as c:
        for tid in (tid1, tid2):
            row = c.execute(sqltext(
                "SELECT plan_code FROM tenant_plans WHERE tenant_id=:t"),
                {"t": tid}).mappings().first()
            assert row is not None, f"{tid} has no tenant_plans row"
            assert row["plan_code"] == "free"


def test_non_admin_member_cannot_read_billing(app_client):
    h = {"Authorization": f"Bearer {token(app_client, 'member@kiam.example', 'secret2')}"}
    r = app_client.get("/api/billing", headers=h)
    assert r.status_code == 403, r.text


def test_billing_endpoint_shape_for_enterprise_tenant(app_client):
    """The KIAM fixture is grandfathered to Enterprise — GET /billing must still work (no
    row-shape assumption breaks on a tenant with no numeric limits)."""
    h = {"Authorization": f"Bearer {token(app_client, 'admin@kiam.example', 'secret1')}"}
    r = app_client.get("/api/billing", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_code"] == "enterprise"
    assert body["limits"]["max_active_audits"] is None
    assert body["usage"]["active_audits"] >= 0
