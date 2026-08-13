"""P5-S9 Slice A — every organisation starts with a control library.

Sumit, after creating a second org: *"when i create a new org then the controls domains are
there, but no checklist is assigned."* The domains were there; the **controls** were not.
`scripts/build_control_library.py` was a one-time bootstrap that only ever ran against the
first install, so every org created through open signup opened Controls to nothing at all.
"""

import uuid

from api.domain import control_library
from api.domain.control_library import F


def _new_org(client, tag):
    r = client.post("/api/auth/signup", json={
        "full_name": f"{tag} owner", "email": f"{tag}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Org {tag}"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j, {"Authorization": f"Bearer {j['access_token']}"}


def test_a_brand_new_organisation_can_use_controls_on_day_one(app_client):
    _org, h = _new_org(app_client, f"lib{uuid.uuid4().hex[:6]}")
    rows = app_client.get("/api/library/controls", headers=h).json()
    assert len(rows) == sum(len(v) for v in F.values()), "the whole curated set should land"

    # …and they are usable, not just present: every one sits in a real domain and carries the
    # fields the Controls screen renders.
    for r in rows[:5]:
        assert r["domain_id"] and r["code"] and r["statement"]
        assert r["lifecycle"] in ("one_time", "recurring", "per_audit")
    assert {r["code"] for r in rows} >= {"AM 3.a", "GRP 1.a"}


def test_nothing_is_marked_not_applicable_for_a_new_organisation(app_client):
    """`DORMANT = {"CS", "AI"}` in the build script marks Cloud Security and AI dormant
    because KIAM is an on-prem vendor. That is a fact about ONE customer. Shipping it as a
    default would silently tell every new cloud-native company that cloud security does not
    apply to them."""
    _org, h = _new_org(app_client, f"appl{uuid.uuid4().hex[:6]}")
    rows = app_client.get("/api/library/controls", headers=h).json()
    assert all(r["applicability"] == "applicable" for r in rows)
    cloud = [r for r in rows if r["code"].startswith("CS ")]
    assert cloud and all(r["applicability"] == "applicable" for r in cloud)


def test_seeding_twice_adds_nothing_and_destroys_nothing(app_client):
    """The seed runs at signup, and the backfill re-runs it for existing tenants — so it has
    to be safely repeatable. Controls become real editable data the moment someone answers
    one, so a seed that cleared first would delete work it never created."""
    from api.core.database import engine

    org, h = _new_org(app_client, f"idem{uuid.uuid4().hex[:6]}")
    tid = org["tenant_id"]
    before = app_client.get("/api/library/controls", headers=h).json()

    # a customer edits one, exactly as they would in the app
    edited = before[0]
    assert app_client.patch(f"/api/library/controls/{edited['id']}", headers=h,
                            json={"statement": "Our own wording"}).status_code == 200

    with engine.begin() as conn:
        assert control_library.seed(conn, tid) == 0, "a second run must add nothing"

    after = app_client.get("/api/library/controls", headers=h).json()
    assert len(after) == len(before)
    assert next(r for r in after if r["id"] == edited["id"])["statement"] == "Our own wording", \
        "re-seeding must not overwrite an edit"


def test_every_curated_control_names_a_real_domain(app_client):
    """The library is keyed by domain CODE and `controls.domain_id` is NOT NULL behind a
    composite FK — a typo'd code would be an IntegrityError in the middle of signup."""
    from api.domain.domains import UNIFIED_DOMAINS

    known = {code for code, _name in UNIFIED_DOMAINS}
    assert set(F) <= known, f"unknown domain codes: {set(F) - known}"


def test_control_codes_are_unique_across_the_library():
    seen = [ref for defs in F.values() for ref, *_ in defs]
    dupes = {r for r in seen if seen.count(r) > 1}
    assert not dupes, f"duplicate control codes would break UNIQUE(tenant_id, code): {dupes}"
