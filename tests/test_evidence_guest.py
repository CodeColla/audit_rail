"""P4-S8 — an auditor guest can read the evidence behind an answer, and nothing else.

Before this sprint `GET /evidence/{id}/file` was the only way to fetch bytes, and it is
gated on `require("evidence","view")` -> `get_current_user`, which 403s any caller whose
kind is not `member`. So an invited bank auditor saw the *titles* of the proof on every
question and could open none of it — the portal's entire purpose, unreachable.

The new route is `GET /assessments/{aid}/evidence/{eid}/file`. Almost every test in this
file is adversarial: the naive fix (authorise on tenant_id, the shape the member route
uses) passes the happy path and hands the bank's auditor the vendor's whole vault. The
tests that would catch that are the ones asserting 404.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_assessments import _seed

PDF = b"%PDF-1.4\nguest evidence\n"


def _kiam_tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _member_h(app_client):
    return {"Authorization": f"Bearer {token(app_client, 'member@kiam.example', 'secret2')}"}


def _admin_h(app_client):
    return {"Authorization": f"Bearer {token(app_client, 'admin@kiam.example', 'secret1')}"}


def _evidence(app_client, h, title=None, body=PDF, name="proof.pdf"):
    r = app_client.post("/api/evidence", headers=h,
                        data={"title": title or f"Ev {uuid.uuid4().hex[:5]}",
                              "evidence_type": "report"},
                        files={"file": (name, body, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _assessment_with_evidence(app_client, engine, suffix):
    """An assessment whose q1 is answered and carries one attached artifact.

    Returns (assessment_id, question_id, evidence_id, member_headers, admin_headers).
    """
    mh, ah = _member_h(app_client), _admin_h(app_client)
    ids = _seed(engine, _kiam_tid(engine), suffix=suffix)
    aid = app_client.post("/api/assessments", headers=mh, json={
        "template_id": ids["tpl"], "title": f"Audit {suffix}"}).json()["id"]
    app_client.put(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=mh,
                   json={"response_value": "yes"})
    eid = _evidence(app_client, mh)
    link = app_client.post(f"/api/assessments/{aid}/responses/{ids['q1']}/evidence",
                           headers=mh, json={"evidence_id": eid})
    assert link.status_code in (200, 201), link.text
    return aid, ids["q1"], eid, mh, ah


def _guest(app_client, ah, aid, email=None):
    inv = app_client.post(f"/api/assessments/{aid}/guests", headers=ah, json={
        "email": email or f"auditor-{uuid.uuid4().hex[:6]}@bank.example",
        "full_name": "A. Auditor", "firm": "PwC", "expires_at": "2027-12-31"})
    assert inv.status_code == 201, inv.text
    return {"Authorization": f"Bearer {inv.json()['access_token']}"}, inv.json()["guest_id"]


def _url(aid, eid):
    return f"/api/assessments/{aid}/evidence/{eid}/file"


# ---------------------------------------------------------------- the happy path

def test_guest_downloads_evidence_attached_to_their_response(app_client):
    from api.database import engine
    aid, _q, eid, _mh, ah = _assessment_with_evidence(app_client, engine, "-g1")
    gh, _ = _guest(app_client, ah, aid)

    r = app_client.get(_url(aid, eid), headers=gh)
    assert r.status_code == 200, r.text
    assert r.content == PDF
    assert "proof.pdf" in r.headers["content-disposition"]


def test_the_download_is_always_an_attachment(app_client):
    """No `disposition` parameter exists on this route. `inline` is what feeds FilePreview,
    whose xlsx branch renders workbook content through innerHTML — not a surface to open
    to an outside auditor, whatever the member route allows."""
    from api.database import engine
    aid, _q, eid, _mh, ah = _assessment_with_evidence(app_client, engine, "-g2")
    gh, _ = _guest(app_client, ah, aid)

    r = app_client.get(_url(aid, eid) + "?disposition=inline", headers=gh)
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


# ---------------------------------------------------------------- the adversarial half

def test_guest_cannot_download_evidence_from_another_assessment_in_the_same_tenant(app_client):
    """THE test. One vendor runs many bank audits at once, so `tenant_id` does not separate
    PwC's engagement from Deloitte's — only `responses.assessment_id` does. An authorisation
    rule filtered on tenant alone passes every other test in this file and fails this one.
    """
    from api.database import engine
    aid_a, _q, eid_a, _mh, ah = _assessment_with_evidence(app_client, engine, "-g3a")
    aid_b, _q2, eid_b, _mh2, _ah2 = _assessment_with_evidence(app_client, engine, "-g3b")
    gh, _ = _guest(app_client, ah, aid_a)

    # their own: fine
    assert app_client.get(_url(aid_a, eid_a), headers=gh).status_code == 200
    # the other audit's artifact, addressed through their own assessment: not found
    assert app_client.get(_url(aid_a, eid_b), headers=gh).status_code == 404
    # and they cannot address the other assessment at all
    assert app_client.get(_url(aid_b, eid_b), headers=gh).status_code == 403


def test_guest_cannot_download_unattached_vault_evidence(app_client):
    """An artifact that exists in the vendor's vault but is attached to no response of this
    assessment — an HR file, another bank's VAPT report — must be unreachable."""
    from api.database import engine
    aid, _q, _eid, mh, ah = _assessment_with_evidence(app_client, engine, "-g4")
    loose = _evidence(app_client, mh, title="Payroll register")
    gh, _ = _guest(app_client, ah, aid)

    assert app_client.get(_url(aid, loose), headers=gh).status_code == 404


def test_guest_cannot_download_control_inherited_evidence(app_client):
    """A deliberate cut, pinned so it cannot drift.

    `response_detail` also returns `inherited_evidence` — artifacts reached through
    `evidence_controls`. Those links are ORG-level: an artifact attached to control X is
    visible from every assessment mapped to X, including a different bank's. Titles already
    travel that way (a pre-existing decision). Bytes must not.
    """
    from api.database import engine
    mh, ah = _member_h(app_client), _admin_h(app_client)
    ids = _seed(engine, _kiam_tid(engine), suffix="-g5")
    aid = app_client.post("/api/assessments", headers=mh, json={
        "template_id": ids["tpl"], "title": "Inherited"}).json()["id"]
    app_client.put(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=mh,
                   json={"response_value": "yes"})
    # attached to the CONTROL q1 maps to, never to the response
    eid = _evidence(app_client, mh, title="Control-level ISP")
    assert app_client.post(f"/api/evidence/{eid}/controls", headers=mh,
                           json={"control_id": ids["ctl1"]}).status_code == 201
    gh, _ = _guest(app_client, ah, aid)

    # the guest can see it listed as inherited...
    det = app_client.get(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=gh).json()
    assert eid in [e["id"] for e in det["inherited_evidence"]]
    # ...and cannot open it
    assert app_client.get(_url(aid, eid), headers=gh).status_code == 404


def test_revoked_guest_cannot_download(app_client):
    """Guest JWTs last up to 30 days, so the grant has to be re-read from the database on
    every request rather than trusted from the token."""
    from api.database import engine
    aid, _q, eid, _mh, ah = _assessment_with_evidence(app_client, engine, "-g6")
    gh, gid = _guest(app_client, ah, aid)
    assert app_client.get(_url(aid, eid), headers=gh).status_code == 200

    assert app_client.delete(f"/api/assessments/{aid}/guests/{gid}",
                             headers=ah).status_code == 204
    assert app_client.get(_url(aid, eid), headers=gh).status_code == 403


def test_expired_guest_grant_cannot_download(app_client):
    from api.database import engine
    aid, _q, eid, _mh, ah = _assessment_with_evidence(app_client, engine, "-g7")
    gh, gid = _guest(app_client, ah, aid)
    with engine.begin() as c:
        c.execute(sqltext("UPDATE assessment_guests SET expires_at='2020-01-01' WHERE id=:g"),
                  {"g": gid})
    assert app_client.get(_url(aid, eid), headers=gh).status_code == 403


def test_guest_token_for_one_assessment_cannot_address_another(app_client):
    from api.database import engine
    aid_a, _q, _e, _mh, ah = _assessment_with_evidence(app_client, engine, "-g8a")
    aid_b, _q2, eid_b, _mh2, _ah2 = _assessment_with_evidence(app_client, engine, "-g8b")
    gh, _ = _guest(app_client, ah, aid_a)
    assert app_client.get(_url(aid_b, eid_b), headers=gh).status_code == 403


def test_a_miss_is_404_never_403(app_client):
    """A 403 on a join miss would confirm that an id exists, handing a guest a
    vault-enumeration oracle they can probe all day."""
    from api.database import engine
    aid, _q, _eid, _mh, ah = _assessment_with_evidence(app_client, engine, "-g9")
    gh, _ = _guest(app_client, ah, aid)
    assert app_client.get(_url(aid, str(uuid.uuid4())), headers=gh).status_code == 404


def test_link_evidence_with_no_file_is_404_not_500(app_client):
    """Every artifact scripts/seed_demo.py creates is medium='LINK' with file_id NULL.
    The join simply misses; it must not raise."""
    from api.database import engine
    aid, q1, _eid, mh, ah = _assessment_with_evidence(app_client, engine, "-g10")
    tid = _kiam_tid(engine)
    link_id = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO evidence (id,tenant_id,title,evidence_type,medium,state,"
            "external_url,created_at) VALUES (:i,:t,'Linked artifact','report','LINK',"
            "'FULFILLED','https://example.invalid/x',now_iso())"), {"i": link_id, "t": tid})
    assert app_client.post(f"/api/assessments/{aid}/responses/{q1}/evidence", headers=mh,
                           json={"evidence_id": link_id}).status_code in (200, 201)
    gh, _ = _guest(app_client, ah, aid)
    assert app_client.get(_url(aid, link_id), headers=gh).status_code == 404


def test_missing_blob_is_410(app_client):
    from api.database import engine
    from api import storage
    aid, _q, eid, mh, ah = _assessment_with_evidence(app_client, engine, "-g11")
    with engine.connect() as c:
        key = c.execute(sqltext(
            "SELECT f.storage_key FROM files f JOIN evidence e ON e.file_id=f.id "
            "WHERE e.id=:e"), {"e": eid}).scalar()
    storage.path_for(key).unlink()
    gh, _ = _guest(app_client, ah, aid)
    assert app_client.get(_url(aid, eid), headers=gh).status_code == 410


# ---------------------------------------------------------------- members, and the old route

def test_a_member_can_use_the_assessment_scoped_route_too(app_client):
    from api.database import engine
    aid, _q, eid, mh, _ah = _assessment_with_evidence(app_client, engine, "-g12")
    r = app_client.get(_url(aid, eid), headers=mh)
    assert r.status_code == 200 and r.content == PDF


def test_a_member_of_another_tenant_gets_404(app_client):
    from api.database import engine
    from api.gstin import checksum
    aid, _q, eid, _mh, _ah = _assessment_with_evidence(app_client, engine, "-g13")
    base = f"27AAPFU{uuid.uuid4().int % 10000:04d}F1Z"
    other = app_client.post("/api/auth/signup", json={
        "full_name": "Outsider", "email": f"out-{uuid.uuid4().hex[:8]}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Other {uuid.uuid4().hex[:6]}",
        "gst_number": base + checksum(base)})
    assert other.status_code == 201, other.text
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert app_client.get(_url(aid, eid), headers=oh).status_code == 404


def test_the_whole_evidence_router_stays_member_only(app_client):
    """The guest's read surface is exactly one route. Every endpoint in the evidence
    router — including the three P4-S8 added — must still refuse a guest, or a future
    shared helper quietly inherits a guest branch onto the tenant's entire vault.
    """
    from api.database import engine
    aid, _q, eid, mh, ah = _assessment_with_evidence(app_client, engine, "-g14")
    gh, _ = _guest(app_client, ah, aid)

    calls = [
        ("get", "/api/evidence", {}),
        ("get", f"/api/evidence/{eid}", {}),
        ("get", f"/api/evidence/{eid}/file", {}),
        ("patch", f"/api/evidence/{eid}", {"json": {"title": "hijacked"}}),
        ("post", f"/api/evidence/{eid}/controls", {"json": {"control_id": "x"}}),
        ("delete", f"/api/evidence/{eid}/controls/x", {}),
        ("delete", f"/api/evidence/{eid}", {}),
    ]
    for method, path, kw in calls:
        r = getattr(app_client, method)(path, headers=gh, **kw)
        assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}"
        assert r.json()["detail"] == "Member access required"

    # and the upload endpoint, which needs a multipart body
    up = app_client.post("/api/evidence", headers=gh,
                         data={"title": "x", "evidence_type": "report"},
                         files={"file": ("x.txt", b"x", "text/plain")})
    assert up.status_code == 403
