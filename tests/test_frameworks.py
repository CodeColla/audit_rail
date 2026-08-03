"""P5-S9 Slice B — certification frameworks over ONE control library.

Sumit asked whether master controls should be organised by certification (SOC 2, HIPAA, ISO).
The answer built here is the one the schema already implied: **no — one library, many clause
tags.** These tests pin the property that makes that worth doing, which is that a single
control counts toward every certification it satisfies, with evidence gathered once.
"""

import uuid


def _new_org(client, tag):
    r = client.post("/api/auth/signup", json={
        "full_name": f"{tag} owner", "email": f"{tag}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Org {tag}"})
    assert r.status_code == 201, r.text
    j = r.json()
    return j, {"Authorization": f"Bearer {j['access_token']}"}


def _fw(client, h) -> dict:
    return {f["code"]: f for f in client.get("/api/frameworks", headers=h).json()}


def _clause(client, h, framework_id, ref) -> dict:
    rows = client.get(f"/api/frameworks/{framework_id}/clauses", headers=h).json()
    return next(c for c in rows if c["ref"] == ref)


def test_a_new_organisation_gets_the_baseline_frameworks(app_client):
    _org, h = _new_org(app_client, f"fw{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    assert {"ISO27001-2022", "SOC2", "RBI-ITO"} <= set(fws)
    for code, f in fws.items():
        assert f["clause_count"] > 0, code
        assert f["coverage_pct"] == 0, "nothing is mapped yet, so nothing is covered"


def test_seeded_clauses_carry_no_licensed_text(app_client):
    """ISO 27001 and the AICPA's TSC are copyrighted. We ship the REFERENCE and short title —
    factual identifiers, like a legal citation — and leave `description` empty for the
    customer to fill from their own licensed copy."""
    _org, h = _new_org(app_client, f"lic{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    for code in ("ISO27001-2022", "SOC2"):
        for cl in app_client.get(f"/api/frameworks/{fws[code]['id']}/clauses",
                                 headers=h).json():
            assert cl["ref"] and cl["title"]
            assert cl["description"] is None, f"{cl['ref']} ships standard text"


def test_one_control_satisfies_two_certifications_at_once(app_client):
    """THE test for this design. A control mapped to ISO A.8.5 and SOC 2 CC6.1 moves both
    coverage figures — which is the entire reason not to keep a separate control set per
    certification."""
    _org, h = _new_org(app_client, f"both{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    iso, soc = fws["ISO27001-2022"], fws["SOC2"]

    control = next(c for c in app_client.get("/api/library/controls", headers=h).json()
                   if c["code"] == "AM 3.a")          # "Strong authentication (SSO / MFA)"
    a85 = _clause(app_client, h, iso["id"], "A.8.5")
    cc61 = _clause(app_client, h, soc["id"], "CC6.1")

    for clause in (a85, cc61):
        r = app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                            json={"clause_id": clause["id"]})
        assert r.status_code == 201, r.text

    after = _fw(app_client, h)
    assert after["ISO27001-2022"]["covered_count"] == 1
    assert after["SOC2"]["covered_count"] == 1
    assert after["RBI-ITO"]["covered_count"] == 0, "an unrelated framework must not move"

    # both clause views name the same single control
    assert [c["code"] for c in _clause(app_client, h, iso["id"], "A.8.5")["controls"]] == ["AM 3.a"]
    assert [c["code"] for c in _clause(app_client, h, soc["id"], "CC6.1")["controls"]] == ["AM 3.a"]

    # linking the same clause twice is refused rather than silently duplicated
    assert app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                           json={"clause_id": a85["id"]}).status_code == 409


def test_readiness_separates_uncovered_from_unproven(app_client):
    """A clause with a control but no evidence is NOT the same as a clause with no control.
    Collapsing them is how a compliance dashboard reports comfort it has not earned."""
    _org, h = _new_org(app_client, f"rdy{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    iso = fws["ISO27001-2022"]

    base = app_client.get(f"/api/frameworks/{iso['id']}/readiness", headers=h).json()
    assert base["summary"]["uncovered"] == base["total"]
    assert base["summary"]["covered"] == 0 and base["summary"]["stale"] == 0

    control = next(c for c in app_client.get("/api/library/controls", headers=h).json()
                   if c["code"] == "AM 3.a")
    a85 = _clause(app_client, h, iso["id"], "A.8.5")
    app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                    json={"clause_id": a85["id"]})

    mapped = app_client.get(f"/api/frameworks/{iso['id']}/readiness", headers=h).json()
    row = next(c for c in mapped["clauses"] if c["ref"] == "A.8.5")
    assert row["state"] == "stale", "mapped but with no evidence is not 'covered'"
    assert mapped["summary"]["stale"] == 1
    assert mapped["summary"]["uncovered"] == base["total"] - 1

    # attach evidence and it becomes genuinely covered. `POST /evidence` is multipart with a
    # required file, and `control_ids` links it in the same call.
    ev = app_client.post("/api/evidence", headers=h,
                         data={"title": "MFA screenshot", "evidence_type": "Screenshot",
                               "control_ids": control["id"]},
                         files={"file": ("mfa.txt", b"screenshot", "text/plain")})
    assert ev.status_code == 201, ev.text

    proven = app_client.get(f"/api/frameworks/{iso['id']}/readiness", headers=h).json()
    assert next(c for c in proven["clauses"] if c["ref"] == "A.8.5")["state"] == "covered"
    assert proven["summary"]["covered"] == 1


def test_a_not_applicable_control_does_not_cover_a_clause(app_client):
    """Marking a control N/A is a Statement-of-Applicability decision. It must stop counting
    as coverage the moment it is made, or an SoA exclusion silently reads as compliance."""
    _org, h = _new_org(app_client, f"na{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    iso = fws["ISO27001-2022"]
    control = next(c for c in app_client.get("/api/library/controls", headers=h).json()
                   if c["code"] == "AM 3.a")
    a85 = _clause(app_client, h, iso["id"], "A.8.5")
    app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                    json={"clause_id": a85["id"]})
    assert _fw(app_client, h)["ISO27001-2022"]["covered_count"] == 1

    r = app_client.patch(f"/api/library/controls/{control['id']}", headers=h, json={
        "applicability": "not_applicable", "na_justification": "No cloud identity provider"})
    assert r.status_code == 200, r.text
    assert _fw(app_client, h)["ISO27001-2022"]["covered_count"] == 0


def test_deleting_a_framework_takes_its_clauses_but_never_a_control(app_client):
    """Our controls are ours; a certification is a lens over them. Removing the lens must not
    remove the thing being looked at."""
    _org, h = _new_org(app_client, f"del{uuid.uuid4().hex[:6]}")
    fws = _fw(app_client, h)
    iso = fws["ISO27001-2022"]
    control = next(c for c in app_client.get("/api/library/controls", headers=h).json()
                   if c["code"] == "AM 3.a")
    a85 = _clause(app_client, h, iso["id"], "A.8.5")
    app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                    json={"clause_id": a85["id"]})

    assert app_client.delete(f"/api/frameworks/{iso['id']}", headers=h).status_code == 200
    assert "ISO27001-2022" not in _fw(app_client, h)
    still = app_client.get(f"/api/library/controls/{control['id']}", headers=h)
    assert still.status_code == 200 and still.json()["code"] == "AM 3.a"


def test_frameworks_do_not_leak_between_organisations(app_client):
    a, ha = _new_org(app_client, f"lka{uuid.uuid4().hex[:6]}")
    b, hb = _new_org(app_client, f"lkb{uuid.uuid4().hex[:6]}")
    mine = _fw(app_client, ha)["ISO27001-2022"]
    app_client.post("/api/frameworks", headers=ha,
                    json={"code": "CUSTOM-A", "name": "Only org A"})
    assert "CUSTOM-A" not in _fw(app_client, hb)
    # and B cannot reach A's framework by id
    assert app_client.get(f"/api/frameworks/{mine['id']}/clauses",
                          headers=hb).status_code == 404


def test_only_control_editors_can_reshape_frameworks(app_client):
    from tests.test_rbac import _member_with_role

    org, h = _new_org(app_client, f"perm{uuid.uuid4().hex[:6]}")
    iso = _fw(app_client, h)["ISO27001-2022"]
    viewer, _ = _member_with_role(app_client, org["tenant_id"], "Viewer")

    assert app_client.get("/api/frameworks", headers=viewer).status_code == 200
    assert app_client.post("/api/frameworks", headers=viewer,
                           json={"code": "NOPE", "name": "No"}).status_code == 403
    assert app_client.post(f"/api/frameworks/{iso['id']}/clauses", headers=viewer,
                           json={"ref": "X.1", "title": "No"}).status_code == 403
    assert app_client.delete(f"/api/frameworks/{iso['id']}", headers=viewer).status_code == 403
