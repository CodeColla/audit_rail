"""P4-S3 — admin-editable dropdown vocabularies."""

import uuid

from sqlalchemy import text as sqltext

from api.vocabularies import KINDS
from tests.test_identity import uniq_gst
from tests.test_rbac import _member_with_role


def _org(client):
    email = f"vocab-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/signup", json={
        "full_name": "Vocab Owner", "email": email, "password": "Passw0rdOne",
        "organisation_name": f"Vocab Org {uuid.uuid4().hex[:6]}", "gst_number": uniq_gst()})
    assert r.status_code == 201, r.text
    return r.json(), {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_new_organisation_starts_with_usable_vocabularies(app_client):
    org, h = _org(app_client)
    out = app_client.get("/api/lookups", headers=h).json()["kinds"]
    assert set(out) == set(KINDS)
    for kind, (label, defaults) in KINDS.items():
        assert out[kind]["label"] == label
        assert len(out[kind]["values"]) == len(defaults), kind
    assert "Access control" in [v["value"] for v in out["risk_category"]["values"]]


def test_values_can_be_added_renamed_and_retired(app_client):
    org, h = _org(app_client)

    made = app_client.post("/api/lookups", headers=h,
                           json={"kind": "risk_category", "value": "Vendor concentration"})
    assert made.status_code == 201, made.text
    lid = made.json()["id"]

    # duplicates within a kind are refused
    dupe = app_client.post("/api/lookups", headers=h,
                           json={"kind": "risk_category", "value": "Vendor concentration"})
    assert dupe.status_code == 409

    assert app_client.patch(f"/api/lookups/{lid}", headers=h,
                            json={"value": "Vendor concentration risk"}).status_code == 200

    # retiring hides it from the default list but keeps the row
    assert app_client.patch(f"/api/lookups/{lid}", headers=h,
                            json={"is_active": 0}).status_code == 200
    live = [v["value"] for v in app_client.get("/api/lookups?kind=risk_category", headers=h)
            .json()["kinds"]["risk_category"]["values"]]
    assert "Vendor concentration risk" not in live
    all_of_them = [v["value"] for v in app_client.get(
        "/api/lookups?kind=risk_category&include_inactive=true", headers=h)
        .json()["kinds"]["risk_category"]["values"]]
    assert "Vendor concentration risk" in all_of_them

    assert app_client.delete(f"/api/lookups/{lid}", headers=h).status_code == 200


def test_unknown_vocabularies_are_rejected(app_client):
    org, h = _org(app_client)
    assert app_client.post("/api/lookups", headers=h,
                           json={"kind": "not_a_vocabulary", "value": "x"}).status_code == 400
    assert app_client.get("/api/lookups?kind=not_a_vocabulary", headers=h).status_code == 400
    assert app_client.post("/api/lookups", headers=h,
                           json={"kind": "risk_category", "value": "   "}).status_code == 400


def test_everyone_can_read_but_only_admins_can_edit(app_client):
    """A Viewer needs the list to render a record's category, but must not reshape it."""
    org, _h = _org(app_client)
    viewer, _ = _member_with_role(app_client, org["tenant_id"], "Viewer")
    assert app_client.get("/api/lookups", headers=viewer).status_code == 200
    assert app_client.post("/api/lookups", headers=viewer,
                           json={"kind": "risk_category", "value": "Nope"}).status_code == 403

    editor, _ = _member_with_role(app_client, org["tenant_id"], "Editor")
    assert app_client.post("/api/lookups", headers=editor,
                           json={"kind": "risk_category", "value": "Nope"}).status_code == 403


def test_vocabularies_do_not_leak_between_organisations(app_client):
    a, ha = _org(app_client)
    b, hb = _org(app_client)
    app_client.post("/api/lookups", headers=ha,
                    json={"kind": "risk_category", "value": "ORG A ONLY"})
    theirs = [v["value"] for v in app_client.get("/api/lookups?kind=risk_category", headers=hb)
              .json()["kinds"]["risk_category"]["values"]]
    assert "ORG A ONLY" not in theirs


def test_files_can_be_served_inline_for_preview(app_client):
    """FilePreview needs `disposition=inline`; downloads must stay `attachment` by default."""
    from api.database import engine
    org, h = _org(app_client)
    # a person to own the document, then a published version with a rendered PDF
    with engine.begin() as c:
        pid = str(uuid.uuid4())
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                          "VALUES (:i,:t,'Doc Owner',:e)"),
                  {"i": pid, "t": org["tenant_id"], "e": f"o-{uuid.uuid4().hex[:6]}@kiam.example"})
    doc = app_client.post("/api/documents", headers=h, json={
        "title": "Inline Preview Policy", "owner_person_id": pid, "content": "# Hello"}).json()

    base = f"/api/documents/{doc['id']}/versions/{doc['version_id']}/render.pdf"
    default = app_client.get(base, headers=h)
    assert default.status_code == 200
    assert default.headers["content-disposition"].startswith("attachment")

    inline = app_client.get(base + "?disposition=inline", headers=h)
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline")
    assert inline.content[:4] == b"%PDF"

    # anything other than the two known values is refused, not passed into the header
    assert app_client.get(base + "?disposition=; rm -rf /", headers=h).status_code == 422


# ─────────────────────────────────────────────────────────── P5-S6

def test_the_new_masters_lists_are_seeded_and_addressable(app_client):
    """The five vocabularies added in S6 — the fields that could not be extended."""
    org, h = _org(app_client)
    for kind in ("department", "position", "evidence_type", "obligation_area", "regulator"):
        r = app_client.get(f"/api/lookups?kind={kind}", headers=h)
        assert r.status_code == 200, f"{kind}: {r.text}"
        values = [v["value"] for v in r.json()["kinds"][kind]["values"]]
        assert values, f"{kind} seeded empty — a blank dropdown is what S6 exists to fix"


def test_a_value_added_to_a_master_is_immediately_offered(app_client):
    """The whole journey, at the API level: add a Department, and the People form's own
    query returns it. The UI failure Sumit reported was this contract being invisible."""
    org, h = _org(app_client)
    app_client.post("/api/lookups", headers=h,
                    json={"kind": "department", "value": "Field Operations North"})
    offered = [v["value"] for v in app_client.get("/api/lookups?kind=department", headers=h)
               .json()["kinds"]["department"]["values"]]
    assert "Field Operations North" in offered

    # …and a person can then actually be saved with it, which is the point of the dropdown.
    made = app_client.post("/api/people", headers=h, json={
        "full_name": "New Starter", "email": f"starter-{uuid.uuid4().hex[:8]}@example.com",
        "department": "Field Operations North"})
    assert made.status_code == 201, made.text
    stored = app_client.get(f"/api/people/{made.json()['id']}", headers=h).json()
    assert stored["department"] == "Field Operations North"


def test_register_enums_match_the_live_check_constraints(app_client):
    """Every enum in `vocabularies` is checked against the constraint it claims to mirror.

    This is the test that pays for itself. The bulk importer (P5-S5) was first written
    against three INVENTED spellings — MITIGATE, ONBOARDING, RESTRICTED — each of which
    passed `one_of()` and then died on a CheckViolation, reporting an opaque database error
    to the user instead of a readable one. Nothing failed until an import was run by hand.

    Reading the constraint out of Postgres means a future edit to either side is caught
    here rather than in production.
    """
    import re

    from sqlalchemy import text as sql

    from api import vocabularies as v
    from api.database import engine

    # constant -> the CHECK constraint it mirrors
    PAIRS = {
        "risks_treatment_check": v.TREATMENTS,
        "risks_status_check": v.RISK_STATUSES,
        "assets_asset_type_check": v.ASSET_TYPES,
        "assets_criticality_check": v.CRITICALITIES,
        "data_items_classification_check": v.CLASSIFICATIONS,
        "third_parties_status_check": v.TP_STATUSES,
        "incidents_status_check": v.INCIDENT_STATUSES,
    }
    with engine.connect() as c:
        for conname, declared in PAIRS.items():
            ddl = c.execute(sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                "WHERE conname = :n"), {"n": conname}).scalar()
            assert ddl, f"no constraint named {conname} — has the schema been renamed?"
            in_db = set(re.findall(r"'([A-Z_]+)'", ddl))
            assert set(declared) == in_db, (
                f"{conname}: code allows {set(declared) - in_db or '—'}, "
                f"database allows {in_db - set(declared) or '—'}")
