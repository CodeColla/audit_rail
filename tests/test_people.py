"""Sprint 1 / M8 — People register.

The DoD from docs/phase3/05-sprint-plan.md, with one correction: the plan says
"create a person with no user_id AND no email → succeeds". The schema makes
`email` NOT NULL, and rightly so — email is the channel a magic-link signing
request travels down (D-SIGN), so a person without one could never attest.
What the D-SIGN premise actually requires is **no login**, which is `user_id`.
"""

import datetime as dt
import io
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token

TODAY = dt.date.today()


def iso(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def test_person_needs_no_login(app_client):
    """The D-SIGN premise: a CMS/field engineer exists with NO user account."""
    h = _h(app_client)
    r = app_client.post("/api/people", headers=h, json={
        "full_name": "Ravi Kumar", "email": "ravi.kumar@kiam.example",
        "department": "Field Ops", "position": "Field Engineer"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    d = app_client.get(f"/api/people/{pid}", headers=h).json()
    assert d["user_id"] is None
    assert d["has_login"] is False
    assert d["effective_state"] == "ACTIVE"


def test_email_is_normalised_and_validated(app_client):
    h = _h(app_client)
    # uppercase is accepted and lowercased (the email_addr domain demands lowercase)
    r = app_client.post("/api/people", headers=h, json={
        "full_name": "Case Test", "email": "Case.Test@KIAM.example"})
    assert r.status_code == 201
    got = app_client.get(f"/api/people/{r.json()['id']}", headers=h).json()
    assert got["email"] == "case.test@kiam.example"
    # junk is a 422 from the validator, not a 500 from the domain
    bad = app_client.post("/api/people", headers=h,
                          json={"full_name": "Bad", "email": "not-an-email"})
    assert bad.status_code == 422


def test_duplicate_email_is_a_friendly_400(app_client):
    h = _h(app_client)
    body = {"full_name": "Dup One", "email": "dup@kiam.example"}
    assert app_client.post("/api/people", headers=h, json=body).status_code == 201
    r = app_client.post("/api/people", headers=h,
                        json={**body, "full_name": "Dup Two"})
    assert r.status_code == 400
    assert "already on the register" in r.json()["detail"]


def test_self_manage_rejected(app_client):
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Solo", "email": "solo@kiam.example"}).json()["id"]
    r = app_client.patch(f"/api/people/{pid}", headers=h, json={"manager_id": pid})
    assert r.status_code == 400
    assert "manage themselves" in r.json()["detail"]


def test_csv_import_reports_bad_rows(app_client):
    """20 good rows + 2 bad ones: bad rows are REPORTED, never silently dropped."""
    h = _h(app_client)
    rows = ["full_name,email,department,position"]
    for i in range(20):
        rows.append(f"Engineer {i},eng{i}@kiam.example,Field Ops,Field Engineer")
    rows.append("Broken Row,not-an-email,Field Ops,Field Engineer")   # bad email
    rows.append(",blank.name@kiam.example,Field Ops,Field Engineer")  # no name
    csv_bytes = "\n".join(rows).encode()

    r = app_client.post("/api/people/import", headers=h,
                        files={"file": ("roster.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 20
    assert body["failed"] == 2
    assert len(body["errors"]) == 2
    assert all("row" in e and "error" in e for e in body["errors"])

    listed = app_client.get("/api/people?department=Field Ops", headers=h).json()
    assert len([p for p in listed if p["source"] == "IMPORT"]) == 20

    depts = app_client.get("/api/people/departments", headers=h).json()
    assert any(d["department"] == "Field Ops" and d["count"] >= 20 for d in depts)


def test_expired_contract_flips_to_inactive(app_client):
    """The register maintains itself — KSL #15 (leaver access)."""
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Leaver", "email": "leaver@kiam.example",
        "contract_end_date": iso(-1)}).json()["id"]

    # reads derive it immediately (contract dates are authoritative)...
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["effective_state"] == "INACTIVE"

    # ...and the maintenance job persists it (scheduler is off in tests)
    gen = app_client.post("/api/tasks/generate", headers=h).json()
    assert gen["people_deactivated"] >= 1
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["state"] == "INACTIVE"

    inactive = app_client.get("/api/people?state=INACTIVE", headers=h).json()
    assert any(p["id"] == pid for p in inactive)


def test_org_chart_builds_the_tree(app_client):
    """VRA #3.1 — organisational chart."""
    h = _h(app_client)
    boss = app_client.post("/api/people", headers=h, json={
        "full_name": "Ops Head", "email": "opshead@kiam.example"}).json()["id"]
    rep = app_client.post("/api/people", headers=h, json={
        "full_name": "Reports To Boss", "email": "reportsto@kiam.example"}).json()["id"]
    assert app_client.patch(f"/api/people/{rep}", headers=h,
                            json={"manager_id": boss}).status_code == 200

    chart = app_client.get("/api/people/org-chart", headers=h).json()
    node = next((n for n in _walk(chart["roots"]) if n["id"] == boss), None)
    assert node is not None
    assert any(r["id"] == rep for r in node["reports"])
    # the report is not also a root
    assert not any(n["id"] == rep for n in chart["roots"])


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["reports"])


def test_cross_tenant_manager_rejected(app_client):
    """The composite FK (manager_id, tenant_id) must stop cross-tenant links."""
    from api.database import engine
    h = _h(app_client)
    other_tenant, other_person = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO tenants (id,name,slug,status,created_at) VALUES (:i,'Other','other','active',:n)"),
                  {"i": other_tenant, "n": "2026-07-16T00:00:00Z"})
        c.execute(sqltext(
            "INSERT INTO people (id,tenant_id,full_name,email) VALUES (:i,:t,'Foreign',"
            "'foreign@other.example')"), {"i": other_person, "t": other_tenant})
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Ours", "email": "ours@kiam.example"}).json()["id"]
    r = app_client.patch(f"/api/people/{pid}", headers=h,
                         json={"manager_id": other_person})
    assert r.status_code == 400
    assert "manager not found" in r.json()["detail"]


def test_people_requires_auth(app_client):
    assert app_client.get("/api/people").status_code == 401


# ────────────────────────────────────────────────── P5-S5: real delete, blocked when cited

def test_deleting_an_unreferenced_person_works(app_client):
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Temp Duplicate", "email": f"dup-{uuid.uuid4().hex[:6]}@kiam.example"}).json()["id"]
    assert app_client.delete(f"/api/people/{pid}", headers=h).status_code == 200
    assert app_client.get(f"/api/people/{pid}", headers=h).status_code == 404


def test_deleting_a_person_who_owns_something_is_409_naming_it(app_client):
    """Sumit's decision was a real delete, blocked when referenced. There are 20+ RESTRICT
    columns pointing at `people`, so the blockers are derived from the Postgres catalog
    rather than a hand-list that would rot the first time a FK is added."""
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Risk Owner", "email": f"owner-{uuid.uuid4().hex[:6]}@kiam.example"}).json()["id"]
    app_client.post("/api/risks", headers=h,
                    json={"title": "Owned risk", "owner_person_id": pid})

    r = app_client.delete(f"/api/people/{pid}", headers=h)
    assert r.status_code == 409, r.text
    # it must say WHAT blocks it, not just "cannot delete"
    assert "risk" in r.json()["detail"]
    assert app_client.get(f"/api/people/{pid}", headers=h).status_code == 200


def test_the_blocker_message_counts_every_kind_of_reference(app_client):
    h = _h(app_client)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Busy Person", "email": f"busy-{uuid.uuid4().hex[:6]}@kiam.example"}).json()["id"]
    app_client.post("/api/risks", headers=h, json={"title": "R1", "owner_person_id": pid})
    app_client.post("/api/assets", headers=h, json={"name": "A1", "owner_person_id": pid})

    detail = app_client.delete(f"/api/people/{pid}", headers=h).json()["detail"]
    assert "risk" in detail and "asset" in detail


def test_a_manager_link_does_not_block_deletion(app_client):
    """`people.manager_id` is ON DELETE SET NULL, so a manager with reports is deletable —
    the reports are orphaned, not refused. Pinning it so the catalog query is not quietly
    widened to treat SET NULL as a blocker."""
    h = _h(app_client)
    boss = app_client.post("/api/people", headers=h, json={
        "full_name": "The Boss", "email": f"boss-{uuid.uuid4().hex[:6]}@kiam.example"}).json()["id"]
    report = app_client.post("/api/people", headers=h, json={
        "full_name": "The Report", "email": f"rep-{uuid.uuid4().hex[:6]}@kiam.example",
        "manager_id": boss}).json()["id"]

    assert app_client.delete(f"/api/people/{boss}", headers=h).status_code == 200
    assert app_client.get(f"/api/people/{report}", headers=h).json()["manager_id"] is None


def test_cannot_delete_a_person_in_another_tenant(app_client):
    h = _h(app_client)
    assert app_client.delete(f"/api/people/{uuid.uuid4()}", headers=h).status_code == 404


def test_a_refused_delete_does_not_break_the_ones_after_it(app_client):
    """P5-S7 — the contract the bulk-delete UI depends on.

    `DataTable` issues N independent DELETEs in a loop, so a 409 in the middle must leave the
    connection and the transaction perfectly usable for the rows that follow. A failed
    statement poisoning its session is a real SQLAlchemy failure mode, and it would show up as
    "the first blocked person makes every later delete fail too" — indistinguishable, from the
    UI, from those people also being referenced.
    """
    h = _h(app_client)

    def person(name):
        return app_client.post("/api/people", headers=h, json={
            "full_name": name,
            "email": f"{name.replace(' ', '')}-{uuid.uuid4().hex[:6]}@kiam.example"},
        ).json()["id"]

    first, blocked, last = person("Batch First"), person("Batch Blocked"), person("Batch Last")
    app_client.post("/api/risks", headers=h,
                    json={"title": "Blocks the middle one", "owner_person_id": blocked})

    # exactly the order the UI sends them in: good, refused, good
    assert app_client.delete(f"/api/people/{first}", headers=h).status_code == 200
    refused = app_client.delete(f"/api/people/{blocked}", headers=h)
    assert refused.status_code == 409, refused.text
    assert app_client.delete(f"/api/people/{last}", headers=h).status_code == 200, \
        "a 409 on the previous row must not take the next one down with it"

    assert app_client.get(f"/api/people/{first}", headers=h).status_code == 404
    assert app_client.get(f"/api/people/{last}", headers=h).status_code == 404
    assert app_client.get(f"/api/people/{blocked}", headers=h).status_code == 200


# ──────────────────────────────────────────────────── P5-S8: logins for people

def _new_org(client, tag):
    r = client.post("/api/auth/signup", json={
        "full_name": f"{tag} owner", "email": f"{tag}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Org {tag}"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j, {"Authorization": f"Bearer {j['access_token']}"}


def test_cannot_take_over_an_existing_account_from_another_tenant(app_client):
    """The reason P5-S8 touched security before it touched UI.

    `users.email` is globally unique, so `invite_person` resolving an account by email finds
    OTHER organisations' users and attaches them. It then minted an invite token for that
    account and returned the raw token in the response body — and `accept_invite` sets the
    password of whoever the token names. Chained, that was a working cross-tenant account
    takeover available to any member holding `users.add` in any organisation:

        add a "person" carrying the victim's email -> invite -> read the token out of your own
        response -> accept it with a password you choose -> sign in as them, in THEIR org.

    Verified end to end before the fix (login returned 200). Now the invite carries no token
    for a pre-existing account, and `accept_invite` refuses to overwrite a live password.
    """
    vtag = f"victim{uuid.uuid4().hex[:6]}"
    victim, _ = _new_org(app_client, vtag)
    victim_email = f"{vtag}@example.com"

    _attacker, ah = _new_org(app_client, f"attacker{uuid.uuid4().hex[:6]}")
    pid = app_client.post("/api/people", headers=ah, json={
        "full_name": "Totally Normal Hire", "email": victim_email}).json()["id"]

    granted = app_client.post(f"/api/people/{pid}/invite", headers=ah, json={})
    assert granted.status_code == 201, granted.text
    body = granted.json()

    # THE SECURITY PROPERTY, asserted by running the whole attack rather than by inspecting
    # the response shape: whatever comes back, the attacker must not end up able to sign in
    # as the victim. (Written this way on purpose — an earlier version asserted only that no
    # token was returned, which fails against the vulnerable code with a KeyError and so
    # proves nothing about the exploit itself.)
    token = body.get("token")
    if token:
        app_client.post("/api/auth/accept-invite",
                        json={"token": token, "password": "Attacker1Pass"})
    app_client.post(f"/api/people/{pid}/invite", headers=ah, json={"password": "Attacker1Pass"})

    assert app_client.post("/api/auth/login", json={
        "email": victim_email, "password": "Attacker1Pass",
        "tenant_id": victim["tenant_id"]}).status_code == 401, \
        "CROSS-TENANT ACCOUNT TAKEOVER: the attacker set the victim's password"
    # …and the victim's own credentials are untouched
    assert app_client.post("/api/auth/login", json={
        "email": victim_email, "password": "Passw0rdOne",
        "tenant_id": victim["tenant_id"]}).status_code == 200

    # the specific defences, once the property above is established
    assert body["existing_account"] is True
    assert body["token"] is None, "no set-password token may be minted for someone else's account"
    assert body["invite_path"] is None


def test_an_invite_cannot_reset_a_password_that_already_exists(app_client):
    """Defence in depth: even holding a valid token, accepting it must not overwrite a live
    password. Belt-and-braces behind the `invite_person` fix, because this endpoint is what
    actually performs the write."""
    tag = f"acc{uuid.uuid4().hex[:6]}"
    _org, h = _new_org(app_client, tag)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Fresh Hire", "email": f"fresh-{tag}@example.com"}).json()["id"]
    token = app_client.post(f"/api/people/{pid}/invite", headers=h, json={}).json()["token"]
    assert token, "a brand-new account SHOULD get a set-password link"

    first = app_client.post("/api/auth/accept-invite",
                            json={"token": token, "password": "TheirOwn1Pass"})
    assert first.status_code == 200, first.text

    # a replayed/second token for the same account cannot re-set it
    again = app_client.post(f"/api/people/{pid}/invite", headers=h, json={})
    assert again.status_code == 409          # already has a login


def _roles(client, h) -> dict:
    return {r["name"]: r["id"] for r in client.get("/api/roles", headers=h).json()}


def test_an_admin_set_password_must_be_changed_at_first_sign_in(app_client):
    """Sumit asked to type a password and hand it over. It works — and it is temporary.

    The product carries legally-meaningful `electronic_signatures`; a live password the admin
    knows makes every signature under it repudiable. So the account is forced through
    ChangePassword before a single screen is reachable, using the existing 30-day expiry
    rather than a new mechanism.
    """
    tag = f"temp{uuid.uuid4().hex[:6]}"
    org, h = _new_org(app_client, tag)
    email = f"hire-{tag}@example.com"
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "New Hire", "email": email}).json()["id"]

    granted = app_client.post(f"/api/people/{pid}/invite", headers=h, json={
        "role_id": _roles(app_client, h)["Editor"], "password": "Temp1234"})
    assert granted.status_code == 201, granted.text
    assert granted.json()["temporary_password"] is True
    assert granted.json()["token"] is None, "a typed password needs no set-password link"

    signed_in = app_client.post("/api/auth/login", json={
        "email": email, "password": "Temp1234", "tenant_id": org["tenant_id"]})
    assert signed_in.status_code == 200, signed_in.text
    th = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

    me = app_client.get("/api/auth/me", headers=th).json()
    assert me["must_change_password"] is True, "the temporary password must not be a lasting one"
    assert me["role"] is not None

    # and once they choose their own, they are through
    changed = app_client.post("/api/auth/change-password", headers=th, json={
        "current_password": "Temp1234", "new_password": "TheirOwn9Pass"})
    assert changed.status_code == 200, changed.text
    after = app_client.post("/api/auth/login", json={
        "email": email, "password": "TheirOwn9Pass", "tenant_id": org["tenant_id"]})
    assert after.status_code == 200
    assert app_client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {after.json()['access_token']}"}).json()["must_change_password"] is False


def test_a_weak_admin_set_password_is_refused_by_the_same_policy(app_client):
    tag = f"weak{uuid.uuid4().hex[:6]}"
    _org, h = _new_org(app_client, tag)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Weak Pw", "email": f"weak-{tag}@example.com"}).json()["id"]
    r = app_client.post(f"/api/people/{pid}/invite", headers=h, json={"password": "letters"})
    assert r.status_code == 400
    assert "letters and numbers" in r.text or "8" in r.text
    # and no half-made login is left behind
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["has_login"] is False


def test_the_role_on_a_login_can_be_changed_and_bites_immediately(app_client):
    tag = f"role{uuid.uuid4().hex[:6]}"
    org, h = _new_org(app_client, tag)
    roles = _roles(app_client, h)
    email = f"mover-{tag}@example.com"
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Role Mover", "email": email}).json()["id"]
    app_client.post(f"/api/people/{pid}/invite", headers=h,
                    json={"role_id": roles["Viewer"], "password": "Temp1234"})
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["role_name"] == "Viewer"

    tok = app_client.post("/api/auth/login", json={
        "email": email, "password": "Temp1234", "tenant_id": org["tenant_id"]}).json()
    th = {"Authorization": f"Bearer {tok['access_token']}"}
    assert "risks.add" not in app_client.get("/api/auth/me", headers=th).json()["permissions"]

    promoted = app_client.patch(f"/api/people/{pid}/login", headers=h,
                                json={"role_id": roles["Editor"]})
    assert promoted.status_code == 200, promoted.text
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["role_name"] == "Editor"
    # permissions resolve per request, so the SAME token now carries the new role
    assert "risks.add" in app_client.get("/api/auth/me", headers=th).json()["permissions"]


def test_revoking_a_login_keeps_the_person_but_ends_the_access(app_client):
    tag = f"revoke{uuid.uuid4().hex[:6]}"
    org, h = _new_org(app_client, tag)
    email = f"leaver-{tag}@example.com"
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "The Leaver", "email": email}).json()["id"]
    app_client.post(f"/api/people/{pid}/invite", headers=h, json={"password": "Temp1234"})
    assert app_client.post("/api/auth/login", json={
        "email": email, "password": "Temp1234", "tenant_id": org["tenant_id"]}).status_code == 200

    gone = app_client.delete(f"/api/people/{pid}/login", headers=h)
    assert gone.status_code == 200, gone.text

    # the PERSON survives — their ownerships, history and signatures are still theirs
    still = app_client.get(f"/api/people/{pid}", headers=h).json()
    assert still["full_name"] == "The Leaver"
    assert still["has_login"] is False and still["role_name"] is None
    # but they can no longer get in
    assert app_client.post("/api/auth/login", json={
        "email": email, "password": "Temp1234",
        "tenant_id": org["tenant_id"]}).status_code == 401


def test_you_cannot_revoke_your_own_access_or_the_super_admins(app_client):
    """Both guards exist because either one missed locks an organisation out of itself."""
    tag = f"lock{uuid.uuid4().hex[:6]}"
    org, h = _new_org(app_client, tag)
    # the signer is both the caller and the Super Admin; give them a person record to aim at
    me = app_client.get("/api/auth/me", headers=h).json()
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "The Owner", "email": f"{tag}@example.com",
        "user_id": me["user_id"]}).json()["id"]

    r = app_client.delete(f"/api/people/{pid}/login", headers=h)
    assert r.status_code == 400
    assert "your own" in r.text.lower() or "super admin" in r.text.lower()
    assert app_client.get(f"/api/people/{pid}", headers=h).json()["has_login"] is True


def test_only_user_admins_can_hand_out_or_take_away_logins(app_client):
    tag = f"perm{uuid.uuid4().hex[:6]}"
    org, h = _new_org(app_client, tag)
    pid = app_client.post("/api/people", headers=h, json={
        "full_name": "Target", "email": f"target-{tag}@example.com"}).json()["id"]

    from tests.test_rbac import _member_with_role
    for role in ("Viewer", "Editor"):
        hh, _ = _member_with_role(app_client, org["tenant_id"], role)
        assert app_client.post(f"/api/people/{pid}/invite", headers=hh,
                               json={"password": "Temp1234"}).status_code == 403, role
        assert app_client.patch(f"/api/people/{pid}/login", headers=hh,
                                json={"role_id": "x"}).status_code == 403, role
        assert app_client.delete(f"/api/people/{pid}/login", headers=hh).status_code == 403, role
