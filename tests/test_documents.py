"""Sprint 2 / M9a — Documents: authoring, versioning, M-of-N approval, publish.

The DoD, made executable. The two that matter most are the Probo fixes:
  • DoD #1 — 2-of-3 threshold: publish blocked at 1 approval, allowed at 2.
  • DoD #2 — a MINOR publish still needs approval (no silent bypass).
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _person(engine, tenant_id, name, email):
    pid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                          "VALUES (:i,:t,:n,:e)"),
                  {"i": pid, "t": tenant_id, "n": name, "e": email})
    return pid


def _setup(app_client):
    from api.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants LIMIT 1")).scalar()
    owner = _person(engine, tid, "Doc Owner", f"owner-{uuid.uuid4().hex[:6]}@kiam.example")
    approvers = [_person(engine, tid, f"Approver {i}",
                         f"appr-{uuid.uuid4().hex[:6]}@kiam.example") for i in range(3)]
    return _h(app_client), owner, approvers


def _new_doc(app_client, h, owner, content="# Purpose\n\nInitial."):
    r = app_client.post("/api/documents", headers=h, json={
        "title": "Information Security Policy", "owner_person_id": owner,
        "document_type": "POLICY", "content": content})
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["version_id"]


def test_create_makes_a_v1_draft(app_client):
    h, owner, _ = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner)
    d = app_client.get(f"/api/documents/{doc_id}", headers=h).json()
    assert d["open_version"]["version_label"] == "1.0"
    assert d["open_version"]["status"] == "DRAFT"
    assert d["current_published_version_id"] is None
    assert d["owner"]["full_name"] == "Doc Owner"


def test_owner_must_be_a_person(app_client):
    h = _h(app_client)
    r = app_client.post("/api/documents", headers=h, json={
        "title": "X", "owner_person_id": str(uuid.uuid4())})
    assert r.status_code == 400
    assert "person" in r.json()["detail"]


def test_threshold_cannot_exceed_approvers(app_client):
    """DoD #3."""
    h, owner, approvers = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner)
    r = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                        json={"threshold_required": 3, "approver_person_ids": approvers[:2]})
    assert r.status_code == 400
    assert "exceeds" in r.json()["detail"]


def test_two_of_three_threshold_gates_publish(app_client):
    """DoD #1 — the headline Probo fix. Publish blocked at 1, allowed at 2."""
    h, owner, approvers = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner)

    sub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 2, "approver_person_ids": approvers})
    assert sub.status_code == 201
    aid = sub.json()["approval_id"]

    # publish with ZERO approvals -> blocked
    p0 = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    assert p0.status_code == 409 and "not enough approvals" in p0.json()["detail"]

    # first approval -> still short of 2
    d1 = app_client.post(f"/api/documents/approvals/{aid}/decide", headers=h,
                        json={"approver_person_id": approvers[0], "state": "APPROVED"}).json()
    assert d1["approved"] == 1 and d1["can_publish"] is False
    p1 = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    assert p1.status_code == 409          # still blocked at 1 of 2

    # second approval -> threshold met
    d2 = app_client.post(f"/api/documents/approvals/{aid}/decide", headers=h,
                        json={"approver_person_id": approvers[1], "state": "APPROVED"}).json()
    assert d2["approved"] == 2 and d2["can_publish"] is True

    # now publish succeeds
    pub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    assert pub.status_code == 200, pub.text
    assert pub.json()["version_label"] == "1.0"
    assert pub.json()["file_id"]

    d = app_client.get(f"/api/documents/{doc_id}", headers=h).json()
    published = next(v for v in d["versions"] if v["version_label"] == "1.0")
    assert published["status"] == "PUBLISHED"
    assert d["current_published_version_id"] == ver_id


def test_reject_sends_it_back_to_draft(app_client):
    h, owner, approvers = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner)
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 2, "approver_person_ids": approvers})
    aid = sub.json()["approval_id"]
    r = app_client.post(f"/api/documents/approvals/{aid}/decide", headers=h,
                       json={"approver_person_id": approvers[0], "state": "REJECTED",
                             "comment": "needs a scope section"}).json()
    assert r["round_status"] == "REJECTED"
    d = app_client.get(f"/api/documents/{doc_id}", headers=h).json()
    assert d["open_version"]["status"] == "DRAFT"     # editable again


def _publish_v1(app_client, h, owner, approvers):
    doc_id, ver_id = _new_doc(app_client, h, owner)
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 1, "approver_person_ids": [approvers[0]]})
    app_client.post(f"/api/documents/approvals/{sub.json()['approval_id']}/decide", headers=h,
                   json={"approver_person_id": approvers[0], "state": "APPROVED"})
    app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    return doc_id


def test_minor_publish_still_needs_approval(app_client):
    """DoD #2 — Probo let a minor bump ship unapproved. We must not."""
    h, owner, approvers = _setup(app_client)
    doc_id = _publish_v1(app_client, h, owner, approvers)

    # edit -> new minor draft 1.1
    nv = app_client.post(f"/api/documents/{doc_id}/versions", headers=h,
                        json={"bump": "minor"}).json()
    assert nv["version_label"] == "1.1"
    vid = nv["version_id"]
    app_client.patch(f"/api/documents/{doc_id}/versions/{vid}", headers=h,
                    json={"content": "# Purpose\n\nRevised for 2026."})

    # try to publish the minor with NO approval round -> blocked (the whole point)
    r = app_client.post(f"/api/documents/{doc_id}/versions/{vid}/publish", headers=h)
    assert r.status_code == 409
    assert "submit the version for approval" in r.json()["detail"]

    # do it properly: submit + approve + publish
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{vid}/submit", headers=h,
                         json={"threshold_required": 1, "approver_person_ids": [approvers[0]]})
    app_client.post(f"/api/documents/approvals/{sub.json()['approval_id']}/decide", headers=h,
                   json={"approver_person_id": approvers[0], "state": "APPROVED"})
    ok = app_client.post(f"/api/documents/{doc_id}/versions/{vid}/publish", headers=h)
    assert ok.status_code == 200 and ok.json()["version_label"] == "1.1"


def test_single_draft_invariant(app_client):
    h, owner, approvers = _setup(app_client)
    doc_id = _publish_v1(app_client, h, owner, approvers)
    assert app_client.post(f"/api/documents/{doc_id}/versions", headers=h,
                          json={"bump": "minor"}).status_code == 201
    # a second open draft is refused
    r = app_client.post(f"/api/documents/{doc_id}/versions", headers=h, json={"bump": "major"})
    assert r.status_code == 409 and "already an open draft" in r.json()["detail"]


def test_published_content_is_frozen(app_client):
    """M5 — the schema must reject editing a PUBLISHED version's bytes."""
    from api.database import engine
    h, owner, approvers = _setup(app_client)
    doc_id = _publish_v1(app_client, h, owner, approvers)
    d = app_client.get(f"/api/documents/{doc_id}", headers=h).json()
    pub_id = d["current_published_version_id"]
    # the API blocks it (not a DRAFT)...
    assert app_client.patch(f"/api/documents/{doc_id}/versions/{pub_id}", headers=h,
                           json={"content": "tampered"}).status_code == 409
    # ...and so does the DB, even on a raw UPDATE
    import psycopg
    with engine.begin() as c:
        try:
            c.execute(sqltext("UPDATE document_versions SET content='tampered' WHERE id=:v"),
                      {"v": pub_id})
            raised = False
        except Exception as e:  # noqa: BLE001
            raised = "immutable" in str(e).lower()
    assert raised, "the freeze trigger did not fire"


def test_pdf_renders_and_diff_works(app_client):
    """DoD #4 (PDF + file_id) and the version diff."""
    h, owner, approvers = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner, content="# Purpose\n\nLine one.")
    # publish v1.0
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 1, "approver_person_ids": [approvers[0]]})
    app_client.post(f"/api/documents/approvals/{sub.json()['approval_id']}/decide", headers=h,
                   json={"approver_person_id": approvers[0], "state": "APPROVED"})
    app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)

    pdf = app_client.get(f"/api/documents/{doc_id}/versions/{ver_id}/render.pdf", headers=h)
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"

    # v1.1 with changed content -> diff shows +/-
    nv = app_client.post(f"/api/documents/{doc_id}/versions", headers=h,
                        json={"bump": "minor"}).json()
    app_client.patch(f"/api/documents/{doc_id}/versions/{nv['version_id']}", headers=h,
                    json={"content": "# Purpose\n\nLine one changed.\nLine two added."})
    diff = app_client.get(f"/api/documents/{doc_id}/diff", headers=h,
                         params={"from_version": ver_id, "to_version": nv["version_id"]}).json()
    assert diff["added"] >= 1 and diff["removed"] >= 1


def test_reject_then_resubmit_publishes_cleanly(app_client):
    """Adversarial-review CONFIRMED defect: a REJECTED round was left alive, and on a
    same-second opened_at tie it could shadow the fresh round — permanently blocking a
    legitimately-approved publish (spurious 409), or crashing it (guard/trigger disagree).
    Submit now cancels the prior round, so exactly one round governs the version."""
    from api.database import engine
    h, owner, approvers = _setup(app_client)
    doc_id, ver_id = _new_doc(app_client, h, owner)
    # round 1 → REJECTED → version back to DRAFT
    s1 = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 1, "approver_person_ids": [approvers[0]]})
    app_client.post(f"/api/documents/approvals/{s1.json()['approval_id']}/decide", headers=h,
                    json={"approver_person_id": approvers[0], "state": "REJECTED"})
    # round 2 (same wall-clock second) → APPROVED → publish must SUCCEED, not 409/500
    s2 = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                         json={"threshold_required": 1, "approver_person_ids": [approvers[0]]})
    app_client.post(f"/api/documents/approvals/{s2.json()['approval_id']}/decide", headers=h,
                    json={"approver_person_id": approvers[0], "state": "APPROVED"})
    pub = app_client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    assert pub.status_code == 200, pub.text
    # exactly one non-cancelled round governs the version; the prior one was voided
    with engine.connect() as c:
        live = c.execute(sqltext("SELECT count(*) FROM document_approvals "
                                 "WHERE document_version_id=:v AND status<>'CANCELLED'"),
                         {"v": ver_id}).scalar()
        cancelled = c.execute(sqltext("SELECT status FROM document_approvals "
                                      "WHERE id=:a"), {"a": s1.json()["approval_id"]}).scalar()
    assert live == 1
    assert cancelled == "CANCELLED"


def test_double_publish_is_a_clean_conflict(app_client):
    """publish() locks the version row (FOR UPDATE) so concurrent publishes serialise;
    the loser sees PUBLISHED and 409s instead of rendering a second, orphaned PDF. This
    checks the sequential guard the lock funnels every racing caller into."""
    h, owner, approvers = _setup(app_client)
    doc_id = _publish_v1(app_client, h, owner, approvers)
    d = app_client.get(f"/api/documents/{doc_id}", headers=h).json()
    pub_id = d["current_published_version_id"]
    r = app_client.post(f"/api/documents/{doc_id}/versions/{pub_id}/publish", headers=h)
    assert r.status_code == 409 and "already published" in r.json()["detail"]


def test_documents_requires_auth(app_client):
    assert app_client.get("/api/documents").status_code == 401
