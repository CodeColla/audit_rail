"""Sprint 3 / M9b — Attestation: audiences, magic-link signing, live coverage %.

The DoD, executable. The public signing route (`/api/sign/{token}`) takes NO auth —
the 256-bit token in the URL is the whole credential — so these tests hit it with a
bare client and prove the token/evidence guarantees D-SIGN promises a bank.

The test DB is session-scoped (people accumulate), so every count here is isolated
behind a unique DEPARTMENT — never ALL_EMPLOYEES — to stay deterministic.
"""

import hashlib
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


def _person(engine, tid, name, department=None):
    pid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email,department) "
                          "VALUES (:i,:t,:n,:e,:d)"),
                  {"i": pid, "t": tid, "n": name,
                   "e": f"{name.lower().replace(' ', '.')}-{uuid.uuid4().hex[:6]}@kiam.example",
                   "d": department})
    return pid


def _publish_doc(app_client, h, owner, title="Acceptable Use Policy",
                 content="# Acceptable Use\n\nLock your screen."):
    r = app_client.post("/api/documents", headers=h, json={
        "title": title, "owner_person_id": owner, "document_type": "POLICY", "content": content})
    doc_id, vid = r.json()["id"], r.json()["version_id"]
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{vid}/submit", headers=h,
                          json={"threshold_required": 1, "approver_person_ids": [owner]})
    app_client.post(f"/api/documents/approvals/{sub.json()['approval_id']}/decide", headers=h,
                    json={"approver_person_id": owner, "state": "APPROVED"})
    p = app_client.post(f"/api/documents/{doc_id}/versions/{vid}/publish", headers=h)
    assert p.status_code == 200, p.text
    return doc_id, vid


def _campaign(app_client, h, engine, tid, dept, n=5, content=None):
    """Publish an AUP, target a fresh DEPARTMENT of n people, start a campaign.
    Returns (doc_id, version_id, {person_id: raw_token})."""
    owner = _person(engine, tid, "Owner")
    kw = {"content": content} if content else {}
    doc_id, vid = _publish_doc(app_client, h, owner, **kw)
    [_person(engine, tid, f"Field Eng {i}", department=dept) for i in range(n)]
    app_client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                    json={"rules": [{"rule": "DEPARTMENT", "value": dept}]})
    camp = app_client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h, json={})
    assert camp.status_code == 201, camp.text
    tokens = {i["person_id"]: i["token"] for i in camp.json()["issued"]}
    return doc_id, vid, tokens


def _sign(app_client, raw, name="Field Eng", **headers):
    return app_client.post(f"/api/sign/{raw}", headers=headers or None,
                           json={"signer_name": name, "agree": True})


# ---------------------------------------------------------------- DoD #4 + #5

def test_coverage_is_computed_live(app_client):
    """DoD #4: DEPARTMENT of 5, 3 sign → 60%; a 6th person drops it to 50% — coverage
    is a live count against the audience, never a stored number."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    dept = f"FieldOps-{uuid.uuid4().hex[:8]}"
    doc_id, vid, tokens = _campaign(app_client, h, engine, tid, dept, n=5)

    for raw in list(tokens.values())[:3]:
        assert _sign(app_client, raw).status_code == 200

    cov = app_client.get(f"/api/documents/{doc_id}/coverage", headers=h).json()
    assert cov["expected"] == 5 and cov["signed"] == 3
    assert cov["coverage_pct"] == 60.0
    assert sum(1 for p in cov["people"] if p["state"] == "SIGNED") == 3

    # a 6th person joins the same department AFTER publish → denominator grows immediately
    _person(engine, tid, "Field Eng late", department=dept)
    cov2 = app_client.get(f"/api/documents/{doc_id}/coverage", headers=h).json()
    assert cov2["expected"] == 6 and cov2["signed"] == 3
    assert cov2["coverage_pct"] == 50.0


def test_signing_page_renders_for_a_person_with_no_login(app_client):
    """DoD #5: the whole point — people have user_id NULL and still sign."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    dept = f"NoLogin-{uuid.uuid4().hex[:8]}"
    doc_id, vid, tokens = _campaign(app_client, h, engine, tid, dept, n=1)
    pid, raw = next(iter(tokens.items()))
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT user_id FROM people WHERE id=:i"), {"i": pid}).scalar() is None
    page = app_client.get(f"/api/sign/{raw}")           # NOTE: no auth header
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["kind"] == "ATTEST"
    assert "Acceptable Use" in body["content"]
    assert body["consent_text"] and body["version_label"] == "1.0"


# ---------------------------------------------------------------- DoD #1 (token lifecycle)

def test_token_is_single_use(app_client):
    """DoD #1: a second redemption of the same link → 410."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Once-{uuid.uuid4().hex[:8]}", n=1)
    raw = next(iter(tokens.values()))
    assert _sign(app_client, raw).status_code == 200
    again = _sign(app_client, raw)
    assert again.status_code == 410 and "already been used" in again.json()["detail"]


def test_expired_token_is_gone(app_client):
    """DoD #1: expiry → 410 (forced by ageing the row)."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Exp-{uuid.uuid4().hex[:8]}", n=1)
    raw = next(iter(tokens.values()))
    with engine.begin() as c:
        c.execute(sqltext("UPDATE signing_tokens SET expires_at='2000-01-01T00:00:00Z' "
                          "WHERE token_hash=:h"),
                  {"h": hashlib.sha256(raw.encode()).hexdigest()})
    r = _sign(app_client, raw)
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert app_client.get(f"/api/sign/{raw}").status_code == 410


def test_recampaign_revokes_the_old_link(app_client):
    """DoD #1: revoked → 410. Re-running a campaign reissues one live link per person and
    revokes the prior one, so an old copied link stops working."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    doc_id, _, tokens = _campaign(app_client, h, engine, tid, f"Rev-{uuid.uuid4().hex[:8]}", n=1)
    old = next(iter(tokens.values()))
    recamp = app_client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h, json={})
    assert recamp.status_code == 201
    new = recamp.json()["issued"][0]["token"]
    r = _sign(app_client, old)
    assert r.status_code == 410 and "revoked" in r.json()["detail"]
    assert _sign(app_client, new).status_code == 200        # the fresh link works


def test_unknown_token_is_404(app_client):
    assert app_client.get("/api/sign/not-a-real-token").status_code == 404
    assert app_client.post("/api/sign/not-a-real-token",
                           json={"signer_name": "X", "agree": True}).status_code == 404


# ---------------------------------------------------------------- DoD #2 (hash only)

def test_only_the_hash_is_stored(app_client):
    """DoD #2: the raw token appears nowhere in the DB; token_hash == sha256(raw)."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Hash-{uuid.uuid4().hex[:8]}", n=1)
    raw = next(iter(tokens.values()))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with engine.connect() as c:
        # the raw value is never stored as the hash …
        assert c.execute(sqltext("SELECT count(*) FROM signing_tokens WHERE token_hash=:r"),
                         {"r": raw}).scalar() == 0
        # … the sha256 is …
        row = c.execute(sqltext("SELECT * FROM signing_tokens WHERE token_hash=:d"),
                        {"d": digest}).mappings().first()
        assert row is not None
        # … and the raw value leaks into no text column of that row.
        assert raw not in " ".join(str(v) for v in row.values())


# ---------------------------------------------------------------- DoD #3 (evidence chain)

def test_signature_captures_the_evidence_chain(app_client):
    """DoD #3: consent_text, signer_ip, signer_user_agent, file_sha256, signed_at."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    doc_id, vid, tokens = _campaign(app_client, h, engine, tid, f"Chain-{uuid.uuid4().hex[:8]}", n=1)
    pid, raw = next(iter(tokens.items()))
    r = _sign(app_client, raw, name="Priya Field",
              **{"X-Forwarded-For": "203.0.113.7", "User-Agent": "KIAM-Test/9.9"})
    assert r.status_code == 200, r.text
    with engine.connect() as c:
        content_sha = c.execute(sqltext("SELECT content_sha256 FROM document_versions WHERE id=:v"),
                                {"v": vid}).scalar()
        es = c.execute(sqltext("SELECT * FROM electronic_signatures WHERE signer_person_id=:p "
                               "ORDER BY signed_at DESC LIMIT 1"), {"p": pid}).mappings().first()
        ds = c.execute(sqltext("SELECT * FROM document_signatures WHERE document_version_id=:v "
                               "AND person_id=:p"), {"v": vid, "p": pid}).mappings().first()
    assert es["signer_name"] == "Priya Field"
    assert str(es["signer_ip"]) == "203.0.113.7"
    assert es["signer_user_agent"] == "KIAM-Test/9.9"
    assert es["file_sha256"] == content_sha            # what was signed == the version's bytes
    assert es["consent_text"] and es["signed_at"]
    assert ds["state"] == "SIGNED" and ds["e_signature_id"] == es["id"]


def test_a_bad_ip_does_not_500(app_client):
    """Starlette's TestClient reports host 'testclient' (not an inet). Storing None
    beats a 500 on the inet column — the signature still records."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Ip-{uuid.uuid4().hex[:8]}", n=1)
    raw = next(iter(tokens.values()))
    r = _sign(app_client, raw)                          # no X-Forwarded-For → host is 'testclient'
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- guards

def test_sign_requires_name_and_agreement_without_burning_the_token(app_client):
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Guard-{uuid.uuid4().hex[:8]}", n=1)
    raw = next(iter(tokens.values()))
    assert app_client.post(f"/api/sign/{raw}",
                           json={"signer_name": "A", "agree": False}).status_code == 400
    assert app_client.post(f"/api/sign/{raw}",
                           json={"signer_name": "   ", "agree": True}).status_code == 400
    # neither failed attempt consumed the token — a proper sign still works
    assert _sign(app_client, raw).status_code == 200


def test_campaign_needs_a_published_version_and_an_audience(app_client):
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    owner = _person(engine, tid, "Owner")
    # unpublished draft → no campaign
    d = app_client.post("/api/documents", headers=h, json={
        "title": "Draft Only", "owner_person_id": owner, "content": "# x"}).json()
    r0 = app_client.post(f"/api/documents/{d['id']}/attestation-campaign", headers=h, json={})
    assert r0.status_code == 400 and "publish" in r0.json()["detail"]
    # published but no audience → no campaign
    doc_id, _ = _publish_doc(app_client, h, owner, title="No Audience Doc")
    r1 = app_client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h, json={})
    assert r1.status_code == 400 and "audience" in r1.json()["detail"]


def test_audience_shape_is_validated(app_client):
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    owner = _person(engine, tid, "Owner")
    doc_id, _ = _publish_doc(app_client, h, owner, title="Shape Doc")
    assert app_client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                           json={"rules": [{"rule": "DEPARTMENT"}]}).status_code == 400
    assert app_client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                           json={"rules": [{"rule": "EXPLICIT"}]}).status_code == 400
    assert app_client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                           json={"rules": [{"rule": "EXPLICIT",
                                            "person_id": str(uuid.uuid4())}]}).status_code == 400


def test_recampaign_skips_the_already_signed(app_client):
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    doc_id, vid, tokens = _campaign(app_client, h, engine, tid, f"Skip-{uuid.uuid4().hex[:8]}", n=3)
    for raw in tokens.values():
        assert _sign(app_client, raw).status_code == 200
    again = app_client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h, json={})
    assert again.status_code == 201
    assert again.json()["already_signed"] == 3 and again.json()["issued"] == []
    cov = app_client.get(f"/api/documents/{doc_id}/coverage", headers=h).json()
    assert cov["signed"] == 3 and cov["coverage_pct"] == 100.0


# ---------------------------------------------------------------- adversarial-review fixes

def test_cannot_sign_a_superseded_version(app_client):
    """CONFIRMED (review): a live link kept signing after its version was superseded — the
    signer would attest to stale bytes. Publishing a new version now revokes the old links,
    and the signing route refuses any non-PUBLISHED version as a backstop."""
    import hashlib
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    owner = _person(engine, tid, "Owner")
    dept = f"Sup-{uuid.uuid4().hex[:8]}"
    _person(engine, tid, "Field Eng", department=dept)
    doc_id, vid = _publish_doc(app_client, h, owner)
    app_client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                    json={"rules": [{"rule": "DEPARTMENT", "value": dept}]})
    raw = app_client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h,
                          json={}).json()["issued"][0]["token"]
    # publish v1.1 → supersedes v1.0
    v2 = app_client.post(f"/api/documents/{doc_id}/versions", headers=h,
                         json={"bump": "minor"}).json()["version_id"]
    app_client.patch(f"/api/documents/{doc_id}/versions/{v2}", headers=h, json={"content": "# AUP v2"})
    sub = app_client.post(f"/api/documents/{doc_id}/versions/{v2}/submit", headers=h,
                          json={"threshold_required": 1, "approver_person_ids": [owner]})
    app_client.post(f"/api/documents/approvals/{sub.json()['approval_id']}/decide", headers=h,
                    json={"approver_person_id": owner, "state": "APPROVED"})
    app_client.post(f"/api/documents/{doc_id}/versions/{v2}/publish", headers=h)

    # the v1.0 link is now revoked
    assert _sign(app_client, raw).status_code == 410
    # backstop: even if a token were somehow still live, the version-status check blocks it
    with engine.begin() as c:
        c.execute(sqltext("UPDATE signing_tokens SET revoked_at=NULL, consumed_at=NULL WHERE token_hash=:h"),
                  {"h": hashlib.sha256(raw.encode()).hexdigest()})
    r = _sign(app_client, raw)
    assert r.status_code == 410 and "superseded" in r.json()["detail"].lower()


def test_exempt_person_leaves_the_coverage_denominator(app_client):
    """CONFIRMED (review): v_document_coverage counted EXEMPT people as expected-but-unsigned,
    so an exemption could never reach 100%. An exemption must excuse, not penalise."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    dept = f"Exempt-{uuid.uuid4().hex[:8]}"
    doc_id, vid, tokens = _campaign(app_client, h, engine, tid, dept, n=3)
    toks = list(tokens.items())
    for _pid, raw in toks[:2]:
        assert _sign(app_client, raw).status_code == 200
    # exempt the third (no API writes EXEMPT yet — set it directly)
    with engine.begin() as c:
        c.execute(sqltext("UPDATE document_signatures SET state='EXEMPT', exempt_reason='on long leave' "
                          "WHERE document_version_id=:v AND person_id=:p"), {"v": vid, "p": toks[2][0]})
    cov = app_client.get(f"/api/documents/{doc_id}/coverage", headers=h).json()
    assert cov["expected"] == 2 and cov["signed"] == 2       # exempt person out of both sides
    assert cov["coverage_pct"] == 100.0


def test_hostile_signer_name_does_not_crash(app_client):
    """CONFIRMED (review): a signer_name with a NUL byte crashed the inet-free public route
    (Postgres text can't store NUL). Control chars are stripped; printable unicode survives."""
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    _, _, tokens = _campaign(app_client, h, engine, tid, f"Nul-{uuid.uuid4().hex[:8]}", n=1)
    pid, raw = next(iter(tokens.items()))
    r = app_client.post(f"/api/sign/{raw}", json={"signer_name": "Ann\x00e\x07 Ådmin", "agree": True})
    assert r.status_code == 200, r.text
    with engine.connect() as c:
        nm = c.execute(sqltext("SELECT signer_name FROM electronic_signatures "
                               "WHERE signer_person_id=:p"), {"p": pid}).scalar()
    assert nm == "Anne Ådmin" and "\x00" not in nm


def test_signing_routes_take_no_auth(app_client):
    # 404 (unknown token), never 401 — the route is deliberately public
    assert app_client.get("/api/sign/whatever").status_code == 404
