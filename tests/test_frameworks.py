"""P5-S9 Slice B — certification frameworks over ONE control library.

Sumit asked whether master controls should be organised by certification (SOC 2, HIPAA, ISO).
The answer built here is the one the schema already implied: **no — one library, many clause
tags.** These tests pin the property that makes that worth doing, which is that a single
control counts toward every certification it satisfies, with evidence gathered once.
"""

import uuid

XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet")


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


# ─────────────────────────────────────────── P5-S10: bring your own clause list

def _xlsx(rows: list[list[str]]) -> bytes:
    import io

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_clauses_can_be_imported_from_a_spreadsheet(app_client):
    """The primary path for any standard we do not ship — and the reason we never have to
    bundle licensed text. A customer with a copy of the standard brings their own wording."""
    _org, h = _new_org(app_client, f"imp{uuid.uuid4().hex[:6]}")
    made = app_client.post("/api/frameworks", headers=h,
                           json={"code": "HIPAA", "name": "HIPAA Security Rule"})
    fid = made.json()["id"]

    cols = app_client.get(f"/api/frameworks/{fid}/import/columns", headers=h)
    assert cols.status_code == 200
    assert [c["key"] for c in cols.json()["columns"]] == ["ref", "title", "description"]

    tpl = app_client.get(f"/api/frameworks/{fid}/import/template.xlsx", headers=h)
    assert tpl.status_code == 200 and tpl.content[:2] == b"PK"

    data = _xlsx([["Reference", "Title", "Description"],
                  ["164.308(a)(1)", "Security management process", "Risk analysis and management"],
                  ["164.312(a)(1)", "Access control", None],
                  ["164.312(e)(1)", "Transmission security", ""]])
    r = app_client.post(f"/api/frameworks/{fid}/import", headers=h,
                        files={"file": ("hipaa.xlsx", data, XLSX_MIME)})
    assert r.status_code == 200, r.text
    assert r.json() == {"created": 3, "failed": 0, "errors": []}

    clauses = app_client.get(f"/api/frameworks/{fid}/clauses", headers=h).json()
    assert [c["ref"] for c in clauses] == ["164.308(a)(1)", "164.312(a)(1)", "164.312(e)(1)"]
    assert clauses[0]["description"] == "Risk analysis and management"
    assert clauses[1]["description"] is None

    # imported clauses behave like seeded ones — mappable, and they count toward coverage
    control = app_client.get("/api/library/controls", headers=h).json()[0]
    assert app_client.post(f"/api/library/controls/{control['id']}/clauses", headers=h,
                           json={"clause_id": clauses[0]["id"]}).status_code == 201
    assert _fw(app_client, h)["HIPAA"]["covered_count"] == 1


def test_one_bad_clause_row_does_not_cost_the_others(app_client):
    """Per-row savepoints, as everywhere else. A 400-clause standard with one typo must not
    be an all-or-nothing failure."""
    _org, h = _new_org(app_client, f"bad{uuid.uuid4().hex[:6]}")
    fid = app_client.post("/api/frameworks", headers=h,
                          json={"code": "PARTIAL", "name": "Partly bad"}).json()["id"]

    data = _xlsx([["Reference", "Title"],
                  ["X.1", "Fine"],
                  ["", "No reference at all"],          # required field missing
                  ["X.1", "Duplicate reference"],       # violates UNIQUE(framework_id, ref)
                  ["X.2", "Also fine"]])
    r = app_client.post(f"/api/frameworks/{fid}/import", headers=h,
                        files={"file": ("p.xlsx", data, XLSX_MIME)}).json()
    assert r["created"] == 2 and r["failed"] == 2
    # Excel-accurate row numbers, or the message points at the wrong line
    assert [e["row"] for e in r["errors"]] == [3, 4]
    assert "Reference is required" in r["errors"][0]["error"]
    assert "already has a clause with that reference" in r["errors"][1]["error"]
    assert [c["ref"] for c in
            app_client.get(f"/api/frameworks/{fid}/clauses", headers=h).json()] == ["X.1", "X.2"]


def test_a_clause_import_will_not_guess_a_column(app_client):
    """Exact header matches only. Deciding that "Clause No." means `ref` is how an import
    quietly fills the wrong column and still reports success."""
    _org, h = _new_org(app_client, f"guess{uuid.uuid4().hex[:6]}")
    fid = app_client.post("/api/frameworks", headers=h,
                          json={"code": "GUESS", "name": "Guessy"}).json()["id"]
    data = _xlsx([["Clause No.", "Heading"], ["1.1", "Something"]])
    r = app_client.post(f"/api/frameworks/{fid}/import", headers=h,
                        files={"file": ("g.xlsx", data, XLSX_MIME)})
    assert r.status_code == 400
    assert "Reference" in r.text and "Title" in r.text

    # …but an explicit mapping is honoured
    import json as _json
    ok = app_client.post(f"/api/frameworks/{fid}/import", headers=h,
                         files={"file": ("g.xlsx", data, XLSX_MIME)},
                         data={"mapping": _json.dumps({"ref": "Clause No.", "title": "Heading"})})
    assert ok.status_code == 200, ok.text
    assert ok.json()["created"] == 1


def test_clause_import_is_gated_and_tenant_scoped(app_client):
    from tests.test_rbac import _member_with_role

    org, h = _new_org(app_client, f"gate{uuid.uuid4().hex[:6]}")
    fid = _fw(app_client, h)["ISO27001-2022"]["id"]
    data = _xlsx([["Reference", "Title"], ["Z.1", "Nope"]])

    viewer, _ = _member_with_role(app_client, org["tenant_id"], "Viewer")
    assert app_client.post(f"/api/frameworks/{fid}/import", headers=viewer,
                           files={"file": ("z.xlsx", data, XLSX_MIME)}).status_code == 403

    _other, oh = _new_org(app_client, f"other{uuid.uuid4().hex[:6]}")
    assert app_client.post(f"/api/frameworks/{fid}/import", headers=oh,
                           files={"file": ("z.xlsx", data, XLSX_MIME)}).status_code == 404
