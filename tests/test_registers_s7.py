"""P4-S7 — Registers enrichment: type-aware assets, the vendor link, data types, incident
timeline and CAPA narrative, and the three previously-dormant columns.

Three columns existed in db/schema.sql with real FKs and ZERO API references before this
sprint — assets.vendor_third_party_id, third_party_agreements.file_id and
third_party_assessments.evidence_id. A bank could not attach a signed contract at all.
Wiring a dormant RESTRICT foreign key also turns previously-passing deletes into 500s,
which is what the delete tests below exist to catch.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_registers import _h, _person, _tid


def _asset(app_client, h, **over):
    body = {"name": f"Asset {uuid.uuid4().hex[:5]}", **over}
    r = app_client.post("/api/assets", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _tp(app_client, h, name=None):
    r = app_client.post("/api/third-parties", headers=h,
                        json={"name": name or f"Vendor {uuid.uuid4().hex[:5]}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _evidence(app_client, h, title=None):
    r = app_client.post("/api/evidence", headers=h,
                        data={"title": title or f"Ev {uuid.uuid4().hex[:5]}",
                              "evidence_type": "REPORT"},
                        files={"file": ("e.txt", b"data", "text/plain")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _incident(app_client, h, **over):
    r = app_client.post("/api/incidents", headers=h,
                        json={"title": f"Inc {uuid.uuid4().hex[:5]}", **over})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- risks: person refs

def test_risk_carries_reporter_and_reviewer(app_client):
    from api.database import engine
    h, tid = _h(app_client), _tid(engine)
    reporter, reviewer = _person(engine, tid, "Reporter"), _person(engine, tid, "Reviewer")
    rid = app_client.post("/api/risks", headers=h, json={
        "title": "Unpatched CMS", "reported_by_person_id": reporter,
        "reviewed_by_person_id": reviewer}).json()["id"]
    d = app_client.get(f"/api/risks/{rid}", headers=h).json()
    assert d["reported_by_name"] == "Reporter" and d["reviewed_by_name"] == "Reviewer"


def test_risk_person_refs_are_tenant_checked_on_create_and_patch(app_client):
    """Only owner_person_id was ever validated; the two new refs would have reached the
    composite FK as an unhandled 500."""
    h = _h(app_client)
    bad = str(uuid.uuid4())
    r = app_client.post("/api/risks", headers=h,
                        json={"title": "x", "reported_by_person_id": bad})
    assert r.status_code == 400 and "reporter" in r.json()["detail"]

    rid = app_client.post("/api/risks", headers=h, json={"title": "y"}).json()["id"]
    p = app_client.patch(f"/api/risks/{rid}", headers=h,
                         json={"reviewed_by_person_id": bad})
    assert p.status_code == 400 and "reviewer" in p.json()["detail"]


# ---------------------------------------------------------------- assets: type-aware

def test_asset_round_trips_type_aware_columns(app_client):
    h = _h(app_client)
    aid = _asset(app_client, h, asset_type="VIRTUAL", subtype="Server",
                 hostname="cms-01", ip_address="10.0.0.5",
                 cloud_provider="AWS", service_url="https://cms.example")
    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert (d["hostname"], d["ip_address"], d["cloud_provider"]) == ("cms-01", "10.0.0.5", "AWS")
    assert d["subtype"] == "Server"

    pid = _asset(app_client, h, asset_type="PHYSICAL", manufacturer="Dell",
                 model="R740", serial_number="SN-9931")
    pd = app_client.get(f"/api/assets/{pid}", headers=h).json()
    assert (pd["manufacturer"], pd["model"], pd["serial_number"]) == ("Dell", "R740", "SN-9931")


def test_physical_asset_cannot_carry_virtual_fields(app_client):
    h = _h(app_client)
    r = app_client.post("/api/assets", headers=h, json={
        "name": "Rack server", "asset_type": "PHYSICAL", "hostname": "nope"})
    assert r.status_code == 400 and "hostname" in r.json()["detail"]


def test_patching_one_field_is_validated_against_the_merged_row(app_client):
    """The subtle one: a PATCH sending only `hostname` carries no asset_type, so a check
    against the submitted dict alone silently passes and the row ends up contradictory."""
    h = _h(app_client)
    aid = _asset(app_client, h, asset_type="PHYSICAL", model="R740")
    r = app_client.patch(f"/api/assets/{aid}", headers=h, json={"hostname": "sneaky"})
    assert r.status_code == 400 and "hostname" in r.json()["detail"]


def test_switching_asset_type_clears_the_other_sides_columns(app_client):
    h = _h(app_client)
    aid = _asset(app_client, h, asset_type="PHYSICAL", manufacturer="Dell",
                 model="R740", serial_number="SN-1")
    assert app_client.patch(f"/api/assets/{aid}", headers=h,
                            json={"asset_type": "VIRTUAL"}).status_code == 200
    d = app_client.get(f"/api/assets/{aid}", headers=h).json()
    assert d["asset_type"] == "VIRTUAL"
    assert d["manufacturer"] is None and d["model"] is None and d["serial_number"] is None


# ---------------------------------------------------------------- assets: vendor link

def test_asset_vendor_link_shows_in_list_and_detail(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h, "Acme Hosting")
    aid = _asset(app_client, h, vendor_third_party_id=tp)
    assert app_client.get(f"/api/assets/{aid}", headers=h).json()["vendor_name"] == "Acme Hosting"
    row = next(a for a in app_client.get("/api/assets", headers=h).json() if a["id"] == aid)
    assert row["vendor_name"] == "Acme Hosting"


def test_unknown_vendor_is_400(app_client):
    h = _h(app_client)
    r = app_client.post("/api/assets", headers=h,
                        json={"name": "x", "vendor_third_party_id": str(uuid.uuid4())})
    assert r.status_code == 400 and "vendor" in r.json()["detail"]


def test_deleting_a_vendor_an_asset_points_at_is_409_not_500(app_client):
    """assets.vendor_third_party_id is ON DELETE RESTRICT. That edge was harmless while the
    column was dormant — wiring it turned a passing delete into an unhandled 500."""
    h = _h(app_client)
    tp = _tp(app_client, h)
    _asset(app_client, h, vendor_third_party_id=tp)
    r = app_client.delete(f"/api/third-parties/{tp}", headers=h)
    assert r.status_code == 409 and "asset" in r.json()["detail"]


# ---------------------------------------------------------------- data inventory

def _data_item(app_client, h, did):
    """Data items have no detail endpoint — the register is list-only by design (P4-S7
    deliberately did not add a drawer for it), so read the row back off the list."""
    return next(x for x in app_client.get("/api/data-items", headers=h).json()
                if x["id"] == did)


def test_data_item_round_trips_data_type(app_client):
    h = _h(app_client)
    did = app_client.post("/api/data-items", headers=h,
                          json={"name": f"Cust {uuid.uuid4().hex[:5]}",
                                "data_type": "Database"}).json()["id"]
    assert _data_item(app_client, h, did)["data_type"] == "Database"
    app_client.patch(f"/api/data-items/{did}", headers=h, json={"data_type": "Object storage"})
    assert _data_item(app_client, h, did)["data_type"] == "Object storage"


# ---------------------------------------------------------------- agreements: the contract file

def _agreement(app_client, h, tp, **over):
    r = app_client.post(f"/api/third-parties/{tp}/agreements", headers=h,
                        json={"kind": "MSA", **over})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_contract_uploads_downloads_and_shows_on_the_vendor(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h)
    ag = _agreement(app_client, h, tp)

    up = app_client.post(f"/api/third-parties/{tp}/agreements/{ag}/file", headers=h,
                         files={"file": ("msa-signed.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert up.status_code == 201, up.text
    assert up.json()["original_name"] == "msa-signed.pdf"

    d = app_client.get(f"/api/third-parties/{tp}", headers=h).json()
    row = next(a for a in d["agreements"] if a["id"] == ag)
    assert row["file_id"] and row["file_name"] == "msa-signed.pdf"

    dl = app_client.get(f"/api/third-parties/{tp}/agreements/{ag}/file", headers=h)
    assert dl.status_code == 200 and dl.content[:4] == b"%PDF"
    assert "msa-signed.pdf" in dl.headers["content-disposition"]


def test_replacing_a_contract_sweeps_the_old_file_row(app_client):
    from api.database import engine
    h = _h(app_client)
    tp = _tp(app_client, h)
    ag = _agreement(app_client, h, tp)
    first = app_client.post(f"/api/third-parties/{tp}/agreements/{ag}/file", headers=h,
                            files={"file": ("v1.pdf", b"one", "application/pdf")}).json()["file_id"]
    app_client.post(f"/api/third-parties/{tp}/agreements/{ag}/file", headers=h,
                    files={"file": ("v2.pdf", b"two", "application/pdf")})
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM files WHERE id=:i"),
                         {"i": first}).scalar() == 0


def test_deleting_an_agreement_sweeps_its_file(app_client):
    from api.database import engine
    h = _h(app_client)
    tp = _tp(app_client, h)
    ag = _agreement(app_client, h, tp)
    fid = app_client.post(f"/api/third-parties/{tp}/agreements/{ag}/file", headers=h,
                          files={"file": ("c.pdf", b"x", "application/pdf")}).json()["file_id"]
    assert app_client.delete(f"/api/third-parties/{tp}/agreements/{ag}",
                             headers=h).status_code == 200
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM files WHERE id=:i"),
                         {"i": fid}).scalar() == 0


def test_agreement_patch_exists_and_is_scoped(app_client):
    """There was no PATCH at all before P4-S7 — a contract was attachable only at the
    instant the agreement was created."""
    h = _h(app_client)
    tp, other = _tp(app_client, h), _tp(app_client, h)
    ag = _agreement(app_client, h, tp, reference="OLD")
    assert app_client.patch(f"/api/third-parties/{tp}/agreements/{ag}", headers=h,
                            json={"reference": "NEW-2026"}).status_code == 200
    d = app_client.get(f"/api/third-parties/{tp}", headers=h).json()
    assert next(a for a in d["agreements"] if a["id"] == ag)["reference"] == "NEW-2026"
    # the same agreement id under the wrong vendor is a 404, not a silent cross-vendor edit
    assert app_client.patch(f"/api/third-parties/{other}/agreements/{ag}", headers=h,
                            json={"reference": "X"}).status_code == 404


def test_agreement_file_id_is_not_settable_from_the_wire(app_client):
    """The FK is composite (file_id, tenant_id); an unvalidated id from a caller would be a
    cross-tenant IntegrityError. The upload endpoint owns that column."""
    h = _h(app_client)
    tp = _tp(app_client, h)
    r = app_client.post(f"/api/third-parties/{tp}/agreements", headers=h,
                        json={"kind": "DPA", "file_id": str(uuid.uuid4())})
    assert r.status_code == 422


# ---------------------------------------------------------------- assessment evidence

def test_assessment_carries_evidence_and_shows_its_title(app_client):
    h = _h(app_client)
    tp, ev = _tp(app_client, h), _evidence(app_client, h, "Vendor questionnaire")
    a = app_client.post(f"/api/third-parties/{tp}/assessments", headers=h,
                        json={"outcome": "PASS", "evidence_id": ev})
    assert a.status_code == 201, a.text
    d = app_client.get(f"/api/third-parties/{tp}", headers=h).json()
    row = next(x for x in d["assessments"] if x["id"] == a.json()["id"])
    assert row["evidence_id"] == ev and row["evidence_title"] == "Vendor questionnaire"


def test_assessment_rejects_unknown_evidence(app_client):
    h = _h(app_client)
    tp = _tp(app_client, h)
    r = app_client.post(f"/api/third-parties/{tp}/assessments", headers=h,
                        json={"evidence_id": str(uuid.uuid4())})
    assert r.status_code == 400 and "evidence" in r.json()["detail"]


# ---------------------------------------------------------------- register evidence joins

def test_risk_evidence_attaches_detaches_and_dedupes(app_client):
    h = _h(app_client)
    rid = app_client.post("/api/risks", headers=h, json={"title": "Data loss"}).json()["id"]
    ev = _evidence(app_client, h, "Backup report")

    assert app_client.post(f"/api/risks/{rid}/evidence", headers=h,
                           json={"evidence_id": ev}).status_code == 201
    assert [e["id"] for e in
            app_client.get(f"/api/risks/{rid}", headers=h).json()["evidence"]] == [ev]
    # the join's PK dedupes a double-click for free
    assert app_client.post(f"/api/risks/{rid}/evidence", headers=h,
                           json={"evidence_id": ev}).status_code == 409
    assert app_client.delete(f"/api/risks/{rid}/evidence/{ev}", headers=h).status_code == 200
    assert app_client.get(f"/api/risks/{rid}", headers=h).json()["evidence"] == []


def test_risk_evidence_tenant_id_comes_from_the_trigger(app_client):
    """These three join tables omit tenant_id on INSERT and rely on inherit_tenant. If the
    trigger is missing the write fails NOT NULL as an opaque 500."""
    from api.database import engine
    h = _h(app_client)
    rid = app_client.post("/api/risks", headers=h, json={"title": "Trigger check"}).json()["id"]
    ev = _evidence(app_client, h)
    app_client.post(f"/api/risks/{rid}/evidence", headers=h, json={"evidence_id": ev})
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT tenant_id FROM risk_evidence WHERE risk_id=:r"),
                        {"r": rid}).scalar()
    assert tid == _tid(engine)


def test_incident_evidence_attaches_and_detaches(app_client):
    h = _h(app_client)
    iid, ev = _incident(app_client, h), _evidence(app_client, h, "Forensic log")
    assert app_client.post(f"/api/incidents/{iid}/evidence", headers=h,
                           json={"evidence_id": ev}).status_code == 201
    assert [e["id"] for e in
            app_client.get(f"/api/incidents/{iid}", headers=h).json()["evidence"]] == [ev]
    assert app_client.delete(f"/api/incidents/{iid}/evidence/{ev}",
                             headers=h).status_code == 200


def test_evidence_join_rejects_unknown_evidence_and_owner(app_client):
    h = _h(app_client)
    rid = app_client.post("/api/risks", headers=h, json={"title": "z"}).json()["id"]
    assert app_client.post(f"/api/risks/{rid}/evidence", headers=h,
                           json={"evidence_id": str(uuid.uuid4())}).status_code == 400
    assert app_client.post(f"/api/risks/{uuid.uuid4()}/evidence", headers=h,
                           json={"evidence_id": str(uuid.uuid4())}).status_code == 404


# ---------------------------------------------------------------- incident timeline

def test_incident_timeline_records_and_orders_events(app_client):
    h = _h(app_client)
    iid = _incident(app_client, h)
    app_client.post(f"/api/incidents/{iid}/events", headers=h, json={
        "event_type": "CONTAINMENT", "body": "Isolated the host",
        "occurred_at": "2026-07-01"})
    app_client.post(f"/api/incidents/{iid}/events", headers=h, json={
        "event_type": "DETECTED", "body": "Alert fired", "occurred_at": "2026-06-30"})

    events = app_client.get(f"/api/incidents/{iid}", headers=h).json()["events"]
    assert [e["event_type"] for e in events] == ["DETECTED", "CONTAINMENT"], \
        "ordered by occurred_at (the real-world time), not insertion order"
    assert events[0]["author_name"] and events[0]["author_kind"] == "member"


def test_incident_event_rejects_a_bad_type_and_an_empty_body(app_client):
    h = _h(app_client)
    iid = _incident(app_client, h)
    assert app_client.post(f"/api/incidents/{iid}/events", headers=h, json={
        "event_type": "GOSSIP", "body": "x"}).status_code == 400
    assert app_client.post(f"/api/incidents/{iid}/events", headers=h, json={
        "event_type": "COMMENT", "body": "   "}).status_code == 400


def test_incident_events_are_immutable_but_the_incident_still_deletes(app_client):
    """deny_update, NOT deny_change: the timeline is tamper-evident, but denying DELETE too
    would make an incident permanently undeletable — the M6 mistake this schema documents."""
    from api.database import engine
    h = _h(app_client)
    iid = _incident(app_client, h)
    eid = app_client.post(f"/api/incidents/{iid}/events", headers=h,
                          json={"body": "original wording"}).json()["id"]

    with engine.begin() as c:
        try:
            c.execute(sqltext("UPDATE incident_events SET body='rewritten' WHERE id=:i"),
                      {"i": eid})
            raised = False
        except Exception as e:                                        # noqa: BLE001
            raised = "append-only" in str(e).lower() or "denied" in str(e).lower()
    assert raised, "a timeline entry must not be editable"

    assert app_client.delete(f"/api/incidents/{iid}", headers=h).status_code == 200
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM incident_events WHERE incident_id=:i"),
                         {"i": iid}).scalar() == 0


def test_incident_carries_resolution_and_corrective_action(app_client):
    h = _h(app_client)
    iid = _incident(app_client, h)
    app_client.patch(f"/api/incidents/{iid}", headers=h, json={
        "resolution": "Rotated the cert and redeployed.",
        "corrective_action": "Add cert-expiry monitoring to the runbook."})
    d = app_client.get(f"/api/incidents/{iid}", headers=h).json()
    assert d["resolution"].startswith("Rotated")
    assert d["corrective_action"].startswith("Add cert-expiry")


def test_closing_an_incident_still_only_needs_a_root_cause(app_client):
    """corrective_action is deliberately NOT a second close gate — one gate is enough, and
    a new one would change what CLOSED means for every incident already in the table."""
    h = _h(app_client)
    iid = _incident(app_client, h)
    assert app_client.patch(f"/api/incidents/{iid}", headers=h,
                            json={"status": "CLOSED"}).status_code == 400
    app_client.patch(f"/api/incidents/{iid}", headers=h, json={"root_cause": "expired cert"})
    assert app_client.patch(f"/api/incidents/{iid}", headers=h,
                            json={"status": "CLOSED"}).status_code == 200


def test_new_register_subroutes_require_auth(app_client):
    assert app_client.post(f"/api/incidents/{uuid.uuid4()}/events",
                           json={"body": "x"}).status_code == 401
    assert app_client.post(f"/api/risks/{uuid.uuid4()}/evidence",
                           json={"evidence_id": "x"}).status_code == 401


# ---------------------------------------------------------------- asset photo

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")


def test_asset_photo_uploads_downloads_and_names_itself(app_client):
    h = _h(app_client)
    a = _asset(app_client, h, asset_type="PHYSICAL")

    assert app_client.get(f"/api/assets/{a}/photo", headers=h).status_code == 404
    up = app_client.post(f"/api/assets/{a}/photo", headers=h,
                         files={"file": ("rack.png", PNG, "image/png")})
    assert up.status_code == 201, up.text

    det = app_client.get(f"/api/assets/{a}", headers=h).json()
    assert det["photo_name"] == "rack.png"
    assert det["photo_file_id"] == up.json()["file_id"]

    dl = app_client.get(f"/api/assets/{a}/photo", headers=h)
    assert dl.status_code == 200 and dl.content[:4] == b"\x89PNG"
    # inline by default — the UI renders it, it does not save it
    assert dl.headers["content-disposition"].startswith("inline")
    assert "rack.png" in dl.headers["content-disposition"]


def test_asset_photo_rejects_a_non_image(app_client):
    """The bytes come back out into an <img>. A PDF or an SVG has no business here."""
    h = _h(app_client)
    a = _asset(app_client, h)
    for name, mime in (("contract.pdf", "application/pdf"),
                       ("logo.svg", "image/svg+xml"),
                       ("notes.txt", "text/plain")):
        r = app_client.post(f"/api/assets/{a}/photo", headers=h,
                            files={"file": (name, b"x", mime)})
        assert r.status_code == 400, f"{mime} -> {r.status_code}"
        assert "PNG" in r.json()["detail"]


def test_replacing_an_asset_photo_sweeps_the_old_file_row(app_client):
    from api.database import engine
    h = _h(app_client)
    a = _asset(app_client, h)
    first = app_client.post(f"/api/assets/{a}/photo", headers=h,
                            files={"file": ("v1.png", PNG, "image/png")}).json()["file_id"]
    app_client.post(f"/api/assets/{a}/photo", headers=h,
                    files={"file": ("v2.png", PNG, "image/png")})
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM files WHERE id=:i"),
                         {"i": first}).scalar() == 0


def test_deleting_an_asset_with_a_photo_does_not_500(app_client):
    """assets.photo_file_id is a RESTRICT foreign key, so a naive DELETE on an asset that
    has a photo would raise an IntegrityError. Same trap as the agreement contract."""
    from api.database import engine
    h = _h(app_client)
    a = _asset(app_client, h)
    fid = app_client.post(f"/api/assets/{a}/photo", headers=h,
                          files={"file": ("rack.png", PNG, "image/png")}).json()["file_id"]
    assert app_client.delete(f"/api/assets/{a}", headers=h).status_code == 200
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM files WHERE id=:i"),
                         {"i": fid}).scalar() == 0


def test_removing_a_photo_leaves_the_asset(app_client):
    h = _h(app_client)
    a = _asset(app_client, h)
    app_client.post(f"/api/assets/{a}/photo", headers=h,
                    files={"file": ("rack.png", PNG, "image/png")})
    assert app_client.delete(f"/api/assets/{a}/photo", headers=h).status_code == 200
    det = app_client.get(f"/api/assets/{a}", headers=h).json()
    assert det["photo_file_id"] is None and det["photo_name"] is None
    # and a second removal is a clean 404, not a 500
    assert app_client.delete(f"/api/assets/{a}/photo", headers=h).status_code == 404


def test_photo_endpoints_are_tenant_scoped(app_client):
    """A photo id from another organisation must 404 — never reach the other tenant's blob.

    RLS is inert in this schema, so every one of these three endpoints has to carry its own
    `tenant_id` predicate. A missed one is a silent cross-tenant read of a photograph of
    someone else's server room.
    """
    from api.gstin import checksum
    h = _h(app_client)
    a = _asset(app_client, h)
    app_client.post(f"/api/assets/{a}/photo", headers=h,
                    files={"file": ("rack.png", PNG, "image/png")})

    base = f"27AAPFU{uuid.uuid4().int % 10000:04d}F1Z"
    other = app_client.post("/api/auth/signup", json={
        "full_name": "Outsider", "email": f"out-{uuid.uuid4().hex[:8]}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Other {uuid.uuid4().hex[:6]}",
        "gst_number": base + checksum(base)})
    assert other.status_code == 201, other.text
    oh = {"Authorization": f"Bearer {other.json()['access_token']}"}

    assert app_client.get(f"/api/assets/{a}/photo", headers=oh).status_code == 404
    assert app_client.post(f"/api/assets/{a}/photo", headers=oh,
                           files={"file": ("x.png", PNG, "image/png")}).status_code == 404
    assert app_client.delete(f"/api/assets/{a}/photo", headers=oh).status_code == 404


def test_timeline_holds_its_order_within_a_single_second(app_client):
    """now_iso() is second-resolution in BOTH the SQL default and api/util.py, so five
    entries written in one request cycle share an identical occurred_at *and* created_at.
    Ordering on those alone leaves Postgres free to return them in any order — which for an
    append-only incident narrative means the record silently rewrites itself between reads.
    incident_events.seq is the tiebreak; this pins it.
    """
    h = _h(app_client)
    i = _incident(app_client, h)
    order = ["DETECTED", "CONTAINMENT", "INVESTIGATION", "CORRECTIVE_ACTION", "RESOLVED"]
    for n, kind in enumerate(order):
        r = app_client.post(f"/api/incidents/{i}/events", headers=h,
                            json={"event_type": kind, "body": f"step {n}"})
        assert r.status_code == 201, r.text

    stamps = {e["occurred_at"] for e in
              app_client.get(f"/api/incidents/{i}", headers=h).json()["events"]}
    assert len(stamps) == 1, "the premise of this test is that the timestamps collide"

    # read it several times — a tie broken by nothing is free to come back differently
    for _ in range(5):
        events = app_client.get(f"/api/incidents/{i}", headers=h).json()["events"]
        assert [e["event_type"] for e in events] == order
        assert [e["body"] for e in events] == [f"step {n}" for n in range(len(order))]


def test_a_backdated_entry_sorts_by_when_it_happened_not_when_it_was_typed(app_client):
    """occurred_at leads the sort: someone writing up an incident after the fact enters the
    real times, and the narrative must read in incident order, not typing order."""
    h = _h(app_client)
    i = _incident(app_client, h)
    app_client.post(f"/api/incidents/{i}/events", headers=h,
                    json={"event_type": "RESOLVED", "body": "restored",
                          "occurred_at": "2026-03-02T09:00:00Z"})
    app_client.post(f"/api/incidents/{i}/events", headers=h,
                    json={"event_type": "DETECTED", "body": "alerted",
                          "occurred_at": "2026-03-01T22:15:00Z"})
    events = app_client.get(f"/api/incidents/{i}", headers=h).json()["events"]
    assert [e["body"] for e in events] == ["alerted", "restored"]


# ────────────────────────────────────────────────── P5-S4: delete safety + incident category

def test_deleting_a_risk_cited_by_a_finding_is_409_not_500(app_client):
    """`findings.risk_id` is ON DELETE RESTRICT (db/schema.sql), and there is no global
    exception handler in api/main.py — so an unguarded delete surfaced as a raw 500.

    Worth being precise about the exposure: **no API route writes `findings.risk_id` today**
    (the insert in assessments.create_finding leaves it null), so this state is currently
    only reachable by a direct write, as done here. The guard is therefore defensive rather
    than a fix for a live incident — but it had to land before the DataTable bulk delete,
    because a bulk run over a register holding one cited risk would otherwise emit a wall of
    500s with nothing legible to report per row.
    """
    from api.database import engine
    h = _h(app_client)
    tid = _tid(engine)
    risk_id = app_client.post("/api/risks", headers=h,
                              json={"title": f"Cited {uuid.uuid4().hex[:5]}"}).json()["id"]

    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO findings (id, tenant_id, title, status, risk_id) "
            "VALUES (:i, :t, 'Cites the risk', 'open', :r)"),
            {"i": str(uuid.uuid4()), "t": tid, "r": risk_id})

    r = app_client.delete(f"/api/risks/{risk_id}", headers=h)
    assert r.status_code == 409, r.text
    assert "finding" in r.json()["detail"]
    # and the risk is still there — a refused delete must not partially apply
    assert app_client.get(f"/api/risks/{risk_id}", headers=h).status_code == 200


def test_an_uncited_risk_still_deletes(app_client):
    """The guard must not turn every delete into a 409."""
    h = _h(app_client)
    risk_id = app_client.post("/api/risks", headers=h,
                              json={"title": f"Free {uuid.uuid4().hex[:5]}"}).json()["id"]
    assert app_client.delete(f"/api/risks/{risk_id}", headers=h).status_code == 200
    assert app_client.get(f"/api/risks/{risk_id}", headers=h).status_code == 404


def test_incident_category_round_trips(app_client):
    """`incident_category` was seeded as a vocabulary in P4-S3 and had no column to land in;
    P5-S4 adds `incidents.category`. Free text by design — the lookup is a UI affordance, so
    an admin can extend it from Masters without a migration."""
    h = _h(app_client)
    iid = _incident(app_client, h, category="Phishing")
    assert app_client.get(f"/api/incidents/{iid}", headers=h).json()["category"] == "Phishing"

    app_client.patch(f"/api/incidents/{iid}", headers=h, json={"category": "Malware"})
    assert app_client.get(f"/api/incidents/{iid}", headers=h).json()["category"] == "Malware"

    # it also reaches the list, which is what the register column renders
    row = next(i for i in app_client.get("/api/incidents", headers=h).json() if i["id"] == iid)
    assert row["category"] == "Malware"


def test_incident_category_is_optional(app_client):
    h = _h(app_client)
    iid = _incident(app_client, h)
    assert app_client.get(f"/api/incidents/{iid}", headers=h).json()["category"] is None
