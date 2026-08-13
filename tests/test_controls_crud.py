"""P4-S5 — the Controls master library: CRUD, retire/restore, and linkage to evidence and
documents. Before this sprint `controls` had zero write endpoints, so `stock_response` —
the "answer once, reuse everywhere" premise the whole product rests on — could not be set
from the product at all.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_identity import uniq_gst
from tests.test_rbac import _member_with_role


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _domain(engine, tenant_id):
    with engine.connect() as c:
        return c.execute(sqltext(
            "SELECT id FROM domains WHERE tenant_id=:t ORDER BY sort_order LIMIT 1"),
            {"t": tenant_id}).scalar()


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _create(client, h, domain_id, **overrides):
    body = {"domain_id": domain_id, "code": f"T {uuid.uuid4().hex[:6]}",
            "statement": "A test control.", "lifecycle": "per_audit", **overrides}
    r = client.post("/api/library/controls", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- create / validate

def test_create_control_minimal(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom)
    d = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert d["status"] == "active" and d["created_at"] == d["updated_at"]


def test_recurring_without_months_is_400_not_500(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": f"T {uuid.uuid4().hex[:6]}", "statement": "x",
        "lifecycle": "recurring"})
    assert r.status_code == 400
    assert "recurrence_months" in r.json()["detail"]


def test_not_applicable_without_justification_is_400(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": f"T {uuid.uuid4().hex[:6]}", "statement": "x",
        "applicability": "not_applicable"})
    assert r.status_code == 400
    assert "not applicable" in r.json()["detail"].lower() or "applicable" in r.json()["detail"]


def test_bad_recurrence_months_is_400(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": f"T {uuid.uuid4().hex[:6]}", "statement": "x",
        "lifecycle": "recurring", "recurrence_months": 0})
    assert r.status_code == 400


def test_cross_tenant_domain_and_owner_are_400(app_client):
    h = _h(app_client)
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": str(uuid.uuid4()), "code": "X 1", "statement": "x"})
    assert r.status_code == 400 and "domain" in r.json()["detail"]

    from api.core.database import engine
    dom = _domain(engine, _tid(engine))
    r2 = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": "X 2", "statement": "x",
        "owner_person_id": str(uuid.uuid4())})
    assert r2.status_code == 400 and "owner" in r2.json()["detail"]


def test_owner_member_id_and_status_are_rejected_on_the_wire(app_client):
    """StrictModel: fields that must never be settable by a caller (owner_member_id has no
    tenant leg on its FK; status is server-controlled) 422 rather than silently ignoring."""
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": "X 3", "statement": "x", "owner_member_id": "x"})
    assert r.status_code == 422
    r2 = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": "X 4", "statement": "x", "status": "retired"})
    assert r2.status_code == 422


def test_duplicate_code_is_409(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    code = f"T {uuid.uuid4().hex[:6]}"
    _create(app_client, h, dom, code=code)
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": code, "statement": "y"})
    assert r.status_code == 409


def test_duplicate_code_against_a_retired_control_names_it(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    code = f"T {uuid.uuid4().hex[:6]}"
    cid = _create(app_client, h, dom, code=code)
    app_client.delete(f"/api/library/controls/{cid}", headers=h)
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": code, "statement": "y"})
    assert r.status_code == 409 and cid in r.json()["detail"]


# ---------------------------------------------------------------- patch

def test_patch_validates_the_merged_row(app_client):
    """PATCH {lifecycle: recurring} on a row with no stored recurrence_months must 400 —
    the CHECK is row-level, evaluated after the merge, not on the submitted patch alone."""
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom, lifecycle="one_time")
    r = app_client.patch(f"/api/library/controls/{cid}", headers=h,
                         json={"lifecycle": "recurring"})
    assert r.status_code == 400


def test_patch_sets_updated_at(app_client):
    """controls has no set_updated_at trigger — this must happen in the app."""
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom)
    before = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    app_client.patch(f"/api/library/controls/{cid}", headers=h,
                     json={"statement": "Updated statement."})
    after = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert after["updated_at"] >= before["updated_at"]
    assert after["statement"] == "Updated statement."


def test_leaving_recurring_clears_recurrence_months(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom, lifecycle="recurring", recurrence_months=6)
    app_client.patch(f"/api/library/controls/{cid}", headers=h, json={"lifecycle": "per_audit"})
    d = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert d["recurrence_months"] is None


def test_patch_stock_response_reports_stale_prefilled_assessments(app_client):
    """Editing the stock answer does not retro-write already-prefilled responses —
    response_revisions is append-only and a bank auditor may already have read it — but
    the caller must be told what is now stale."""
    from api.core.database import engine
    tid = _tid(engine)
    dom = _domain(engine, tid)
    h = _h(app_client)
    cid = _create(app_client, h, dom, stock_response="yes", stock_comment="Initial.")

    tpl, qid = str(uuid.uuid4()), str(uuid.uuid4())
    now = "2026-07-11T00:00:00Z"
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,"
                          "version_label,status,created_at) VALUES "
                          "(:i,:t,'TestBank','T','v1','active',:n)"),
                  {"i": tpl, "t": tid, "n": now})
        c.execute(sqltext("INSERT INTO questions (id,template_id,number,text,sort_order) "
                          "VALUES (:i,:tp,'1','Q?',1)"), {"i": qid, "tp": tpl})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,1.0,'confirmed',:n)"),
                  {"i": str(uuid.uuid4()), "q": qid, "c": cid, "n": now})

    aid = app_client.post("/api/assessments", headers=h,
                          json={"template_id": tpl, "title": "Stale test"}).json()["id"]
    pf = app_client.post(f"/api/assessments/{aid}/prefill", headers=h).json()
    assert pf["prefilled"] == 1

    r = app_client.patch(f"/api/library/controls/{cid}", headers=h,
                         json={"stock_response": "no"})
    assert r.status_code == 200
    assert r.json()["stale_prefilled"]["count"] == 1
    assert r.json()["stale_prefilled"]["assessments"][0]["id"] == aid

    # and the response itself was NOT rewritten
    d = app_client.get(f"/api/assessments/{aid}/responses/{qid}", headers=h).json()
    assert d["response"]["response_value"] == "yes"


# ---------------------------------------------------------------- retire / restore

def test_retire_hides_from_list_but_detail_and_restore_still_work(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom)

    r = app_client.delete(f"/api/library/controls/{cid}", headers=h)
    assert r.status_code == 200

    codes = {c["id"] for c in app_client.get("/api/library/controls", headers=h).json()}
    assert cid not in codes
    codes_all = {c["id"] for c in app_client.get(
        "/api/library/controls?include_retired=true", headers=h).json()}
    assert cid in codes_all

    d = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert d["status"] == "retired"

    assert app_client.delete(f"/api/library/controls/{cid}", headers=h).status_code == 409

    assert app_client.post(f"/api/library/controls/{cid}/restore", headers=h).status_code == 200
    d2 = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert d2["status"] == "active"
    assert app_client.post(f"/api/library/controls/{cid}/restore",
                           headers=h).status_code == 409


def test_retiring_pauses_its_active_recurring_task_and_restoring_resumes_it(app_client):
    from api.core.database import engine
    tid = _tid(engine)
    h = _h(app_client)
    dom = _domain(engine, tid)
    cid = _create(app_client, h, dom, lifecycle="recurring", recurrence_months=3)

    task_id = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO tasks (id,tenant_id,control_id,title,cadence_months,"
            "next_due_at,status,created_at) VALUES "
            "(:i,:t,:c,'Recurring check',3,'2026-08-01T00:00:00Z','active',:n)"),
            {"i": task_id, "t": tid, "c": cid, "n": "2026-07-11T00:00:00Z"})

    r = app_client.delete(f"/api/library/controls/{cid}", headers=h)
    assert r.json()["retained"]["tasks_paused"] == 1
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT status FROM tasks WHERE id=:i"),
                         {"i": task_id}).scalar() == "paused"

    app_client.post(f"/api/library/controls/{cid}/restore", headers=h)
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT status FROM tasks WHERE id=:i"),
                         {"i": task_id}).scalar() == "active"


def test_retired_control_does_not_prefill_or_show_as_mapped(app_client):
    from api.core.database import engine
    tid = _tid(engine)
    h = _h(app_client)
    dom = _domain(engine, tid)
    cid = _create(app_client, h, dom, stock_response="yes")

    tpl, qid = str(uuid.uuid4()), str(uuid.uuid4())
    now = "2026-07-11T00:00:00Z"
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,"
                          "version_label,status,created_at) VALUES "
                          "(:i,:t,'RetireBank','T','v1','active',:n)"),
                  {"i": tpl, "t": tid, "n": now})
        c.execute(sqltext("INSERT INTO questions (id,template_id,number,text,sort_order) "
                          "VALUES (:i,:tp,'1','Q?',1)"), {"i": qid, "tp": tpl})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,1.0,'confirmed',:n)"),
                  {"i": str(uuid.uuid4()), "q": qid, "c": cid, "n": now})

    app_client.delete(f"/api/library/controls/{cid}", headers=h)
    aid = app_client.post("/api/assessments", headers=h,
                          json={"template_id": tpl, "title": "Retired-control test"}).json()["id"]
    pf = app_client.post(f"/api/assessments/{aid}/prefill", headers=h).json()
    assert pf["prefilled"] == 0
    grid = {r["number"]: r for r in
            app_client.get(f"/api/assessments/{aid}/questions", headers=h).json()}
    assert grid["1"]["mapped_control"] is None


# ---------------------------------------------------------------- evidence / document linkage

def _evidence(app_client, h, title="Test evidence"):
    r = app_client.post("/api/evidence", headers=h,
                        data={"title": title, "evidence_type": "SCREENSHOT"},
                        files={"file": ("e.txt", b"data", "text/plain")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_evidence_links_both_ways_and_rejects_duplicates(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom)
    eid = _evidence(app_client, h)

    r = app_client.post(f"/api/library/controls/{cid}/evidence", headers=h,
                        json={"evidence_id": eid})
    assert r.status_code == 201

    cd = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert any(e["id"] == eid for e in cd["linked_evidence"])
    ed = app_client.get(f"/api/evidence/{eid}", headers=h).json()
    assert any(c["id"] == cid for c in ed["linked_controls"])

    dup = app_client.post(f"/api/library/controls/{cid}/evidence", headers=h,
                          json={"evidence_id": eid})
    assert dup.status_code == 409

    unlink = app_client.delete(f"/api/library/controls/{cid}/evidence/{eid}", headers=h)
    assert unlink.status_code == 200
    cd2 = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    assert not any(e["id"] == eid for e in cd2["linked_evidence"])
    # idempotent
    assert app_client.delete(f"/api/library/controls/{cid}/evidence/{eid}",
                             headers=h).status_code == 200


def test_unknown_or_cross_tenant_evidence_is_400(app_client):
    from api.core.database import engine
    h = _h(app_client)
    dom = _domain(engine, _tid(engine))
    cid = _create(app_client, h, dom)
    r = app_client.post(f"/api/library/controls/{cid}/evidence", headers=h,
                        json={"evidence_id": str(uuid.uuid4())})
    assert r.status_code == 400


def test_document_links_both_ways_and_survives_archiving(app_client):
    """No lifecycle filter on linked_documents — a control silently losing its policy the
    moment someone archives it is exactly the failure the control page exists to catch."""
    from api.core.database import engine
    h = _h(app_client)
    tid = _tid(engine)
    dom = _domain(engine, tid)
    cid = _create(app_client, h, dom)
    owner = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                          "VALUES (:i,:t,:n,:e)"),
                  {"i": owner, "t": tid, "n": "Doc Owner",
                   "e": f"owner-{uuid.uuid4().hex[:6]}@kiam.example"})
    doc_id = app_client.post("/api/documents", headers=h, json={
        "title": "Access Policy", "owner_person_id": owner,
        "document_type": "POLICY"}).json()["id"]

    r = app_client.post(f"/api/library/controls/{cid}/documents", headers=h,
                        json={"document_id": doc_id, "note": "primary policy"})
    assert r.status_code == 201
    dup = app_client.post(f"/api/library/controls/{cid}/documents", headers=h,
                          json={"document_id": doc_id})
    assert dup.status_code == 409

    app_client.patch(f"/api/documents/{doc_id}", headers=h, json={"status": "ARCHIVED"})
    cd = app_client.get(f"/api/library/controls/{cid}", headers=h).json()
    linked = next(d for d in cd["linked_documents"] if d["id"] == doc_id)
    assert linked["status"] == "ARCHIVED"

    assert app_client.delete(f"/api/library/controls/{cid}/documents/{doc_id}",
                             headers=h).status_code == 200


# ---------------------------------------------------------------- permissions
#
# A fresh org (not KIAM) so these also pin that domains.seed() runs at signup — before
# P4-S5, a brand-new organisation got zero domains and "Add control" was a dead end.

def _fresh_org(app_client):
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    r = app_client.post("/api/auth/signup", json={
        "full_name": "Owner", "email": email, "password": "Passw0rdOne",
        "organisation_name": f"Controls Org {uuid.uuid4().hex[:6]}", "gst_number": uniq_gst()})
    assert r.status_code == 201, r.text
    org = r.json()
    return org["tenant_id"], {"Authorization": f"Bearer {org['access_token']}"}


def test_viewer_cannot_write_controls(app_client):
    from api.core.database import engine
    tid, admin_h = _fresh_org(app_client)
    dom = _domain(engine, tid)
    assert dom, "a fresh org must have the 16-domain framework seeded at signup"
    h, _ = _member_with_role(app_client, tid, "Viewer")
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": "V 1", "statement": "x"})
    assert r.status_code == 403


def test_editor_can_edit_but_not_retire(app_client):
    """Editor holds every action except delete on this codebase's permission model — retire
    IS gated on controls.delete, so an Editor must be refused (pins the model, not a bug)."""
    from api.core.database import engine
    tid, admin_h = _fresh_org(app_client)
    dom = _domain(engine, tid)
    cid = _create(app_client, admin_h, dom)
    h, _ = _member_with_role(app_client, tid, "Editor")
    assert app_client.patch(f"/api/library/controls/{cid}", headers=h,
                            json={"statement": "edited by editor"}).status_code == 200
    assert app_client.delete(f"/api/library/controls/{cid}", headers=h).status_code == 403


def test_writes_require_auth(app_client):
    assert app_client.post("/api/library/controls", json={
        "domain_id": "x", "code": "x", "statement": "x"}).status_code == 401
