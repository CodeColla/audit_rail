"""P4-S8 — the evidence vault becomes editable.

Evidence was insert-once/delete-only: no PATCH anywhere in `api/`, and controls could be
linked only in the multipart body of the original upload. A typo in a title meant deleting
the artifact and uploading it again — which, once anything referenced it, a RESTRICT FK
refused outright.
"""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token
from tests.test_identity import uniq_gst

PDF = b"%PDF-1.4\ns8\n"


def _h(app_client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(app_client, who, pw)}"}


def _ev(app_client, h, **over):
    data = {"title": f"Ev {uuid.uuid4().hex[:5]}", "evidence_type": "report"}
    data.update({k: v for k, v in over.items() if v is not None})
    r = app_client.post("/api/evidence", headers=h, data=data,
                        files={"file": ("proof.pdf", PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _a_control(app_client, h, **over):
    """The conftest test database seeds domains but ZERO controls — the ~100-control
    library comes from scripts/build_control_library.py, which only init_db/seed_e2e run.
    Every control a test needs must therefore be created through the API."""
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    from api.database import engine
    with engine.connect() as c:
        dom = c.execute(sqltext("SELECT id FROM domains WHERE tenant_id=:t "
                                "ORDER BY sort_order LIMIT 1"), {"t": tid}).scalar()
    r = app_client.post("/api/library/controls", headers=h, json={
        "domain_id": dom, "code": f"S8 {uuid.uuid4().hex[:6]}",
        "statement": "A control created for the S8 suite.", "lifecycle": "per_audit", **over})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _other_org(app_client):
    r = app_client.post("/api/auth/signup", json={
        "full_name": "Outsider", "email": f"out-{uuid.uuid4().hex[:8]}@example.com",
        "password": "Passw0rdOne", "organisation_name": f"Other {uuid.uuid4().hex[:6]}",
        "gst_number": uniq_gst()})
    assert r.status_code == 201, r.text
    return r.json()["tenant_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------- PATCH

def test_patch_updates_title_type_and_dates(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    r = app_client.patch(f"/api/evidence/{e}", headers=h, json={
        "title": "ISO 27001 Certificate 2026", "evidence_type": "certificate",
        "issued_at": "2026-01-15", "valid_until": "2027-01-14"})
    assert r.status_code == 200, r.text
    got = app_client.get(f"/api/evidence/{e}", headers=h).json()
    assert got["title"] == "ISO 27001 Certificate 2026"
    assert got["evidence_type"] == "certificate"
    assert got["issued_at"] == "2026-01-15" and got["valid_until"] == "2027-01-14"


def test_patch_leaves_unsent_fields_alone(app_client):
    """exclude_unset — sending only a title must not null the dates."""
    h = _h(app_client)
    e = _ev(app_client, h, issued_at="2026-02-01", valid_until="2026-12-31")
    app_client.patch(f"/api/evidence/{e}", headers=h, json={"title": "Renamed"})
    got = app_client.get(f"/api/evidence/{e}", headers=h).json()
    assert got["title"] == "Renamed"
    assert got["issued_at"] == "2026-02-01" and got["valid_until"] == "2026-12-31"


def test_patch_with_an_empty_body_is_a_noop(app_client):
    h = _h(app_client)
    e = _ev(app_client, h, title="Untouched")
    assert app_client.patch(f"/api/evidence/{e}", headers=h, json={}).status_code == 200
    assert app_client.get(f"/api/evidence/{e}", headers=h).json()["title"] == "Untouched"


def test_patch_empty_string_date_clears_the_field(app_client):
    h = _h(app_client)
    e = _ev(app_client, h, valid_until="2026-12-31")
    assert app_client.patch(f"/api/evidence/{e}", headers=h,
                            json={"valid_until": ""}).status_code == 200
    assert app_client.get(f"/api/evidence/{e}", headers=h).json()["valid_until"] is None


def test_patch_rejects_an_unknown_field(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    r = app_client.patch(f"/api/evidence/{e}", headers=h, json={"titel": "typo"})
    assert r.status_code == 422


def test_patch_cannot_repoint_the_artifact_at_other_bytes(app_client):
    """ADVERSARIAL. `file_id` is absent from the allow-list, so StrictModel turns this into
    a 422. Without the allow-list it would be an update that hands one evidence row another
    row's blob — and `document_version_id` / `requirement_id` would reach composite FKs."""
    h = _h(app_client)
    a, b = _ev(app_client, h), _ev(app_client, h)
    with __import__("api.database", fromlist=["engine"]).engine.connect() as c:
        other_file = c.execute(sqltext("SELECT file_id FROM evidence WHERE id=:i"),
                               {"i": b}).scalar()
    for field, value in (("file_id", other_file), ("document_version_id", str(uuid.uuid4())),
                         ("requirement_id", str(uuid.uuid4())), ("tenant_id", "x")):
        r = app_client.patch(f"/api/evidence/{a}", headers=h, json={field: value})
        assert r.status_code == 422, f"{field} -> {r.status_code}"


def test_patch_cannot_break_the_medium_check(app_client):
    """ADVERSARIAL. `ev_medium_matches` requires a LINK row to keep an external_url and a
    FILE row to keep a file_id. Both columns are off the allow-list, so a PATCH cannot
    reach that CHECK — which would otherwise be an uncaught IntegrityError, i.e. a 500."""
    h = _h(app_client)
    e = _ev(app_client, h)
    for field, value in (("medium", "LINK"), ("state", "REQUESTED"),
                         ("external_url", "https://example.invalid/x")):
        r = app_client.patch(f"/api/evidence/{e}", headers=h, json={field: value})
        assert r.status_code == 422, f"{field} -> {r.status_code}"


def test_patch_blanking_a_not_null_column_is_400_not_500(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    for field, word in (("title", "title"), ("evidence_type", "evidence type")):
        r = app_client.patch(f"/api/evidence/{e}", headers=h, json={field: "   "})
        assert r.status_code == 400, f"{field} -> {r.status_code} {r.text}"
        assert word in r.json()["detail"]


def test_patch_malformed_date_is_422(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    assert app_client.patch(f"/api/evidence/{e}", headers=h,
                            json={"valid_until": "31-12-2026"}).status_code == 422


def test_patch_another_tenants_evidence_is_404(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    _tid, oh = _other_org(app_client)
    assert app_client.patch(f"/api/evidence/{e}", headers=oh,
                            json={"title": "stolen"}).status_code == 404
    assert app_client.get(f"/api/evidence/{e}", headers=h).json()["title"] != "stolen"


def test_patch_requires_evidence_edit(app_client):
    from tests.test_rbac import _member_with_role
    _tid, oh = _other_org(app_client)
    e = _ev(app_client, oh)
    tid = app_client.get("/api/auth/me", headers=oh).json()["tenant_id"]
    vh, _ = _member_with_role(app_client, tid, "Viewer")
    assert app_client.patch(f"/api/evidence/{e}", headers=vh,
                            json={"title": "nope"}).status_code == 403


# ---------------------------------------------------------------- control links

def test_attach_then_detach_a_control(app_client):
    h = _h(app_client)
    e, c = _ev(app_client, h), _a_control(app_client, h)
    assert app_client.post(f"/api/evidence/{e}/controls", headers=h,
                           json={"control_id": c}).status_code == 201
    assert [x["id"] for x in
            app_client.get(f"/api/evidence/{e}", headers=h).json()["linked_controls"]] == [c]
    assert app_client.get("/api/evidence", headers=h).json() and any(
        r["id"] == e and r["linked_controls"] == 1
        for r in app_client.get("/api/evidence", headers=h).json())

    assert app_client.delete(f"/api/evidence/{e}/controls/{c}", headers=h).status_code == 200
    assert app_client.get(f"/api/evidence/{e}", headers=h).json()["linked_controls"] == []


def test_attaching_the_same_control_twice_is_409(app_client):
    h = _h(app_client)
    e, c = _ev(app_client, h), _a_control(app_client, h)
    app_client.post(f"/api/evidence/{e}/controls", headers=h, json={"control_id": c})
    r = app_client.post(f"/api/evidence/{e}/controls", headers=h, json={"control_id": c})
    assert r.status_code == 409 and "already linked" in r.json()["detail"]


def test_attaching_an_unknown_control_is_400(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    r = app_client.post(f"/api/evidence/{e}/controls", headers=h,
                        json={"control_id": str(uuid.uuid4())})
    assert r.status_code == 400 and "not found in this organisation" in r.json()["detail"]


def test_attaching_another_tenants_control_is_400_not_500(app_client):
    """ADVERSARIAL. evidence_controls' FKs are composite (control_id, tenant_id), so a
    cross-tenant id would reach the database as an uncaught IntegrityError."""
    h = _h(app_client)
    e = _ev(app_client, h)
    _tid, oh = _other_org(app_client)
    foreign = _a_control(app_client, oh)
    r = app_client.post(f"/api/evidence/{e}/controls", headers=h, json={"control_id": foreign})
    assert r.status_code == 400, r.text


def test_attaching_to_another_tenants_evidence_is_404(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    _tid, oh = _other_org(app_client)
    c = _a_control(app_client, oh)
    assert app_client.post(f"/api/evidence/{e}/controls", headers=oh,
                           json={"control_id": c}).status_code == 404


def test_detach_is_idempotent(app_client):
    h = _h(app_client)
    e, c = _ev(app_client, h), _a_control(app_client, h)
    assert app_client.delete(f"/api/evidence/{e}/controls/{c}", headers=h).status_code == 200


def test_detach_needs_only_edit_not_delete(app_client):
    """Unlinking removes a relationship, not a record — an Editor must be able to undo a
    link they just made without holding evidence.delete."""
    from tests.test_rbac import _member_with_role
    _tid, oh = _other_org(app_client)
    tid = app_client.get("/api/auth/me", headers=oh).json()["tenant_id"]
    e, c = _ev(app_client, oh), _a_control(app_client, oh)
    app_client.post(f"/api/evidence/{e}/controls", headers=oh, json={"control_id": c})
    eh, _ = _member_with_role(app_client, tid, "Editor")
    assert app_client.delete(f"/api/evidence/{e}/controls/{c}", headers=eh).status_code == 200


def test_both_doors_write_the_same_join_table(app_client):
    """The control side (library.py) and the evidence side write the same rows. Proving it
    keeps the two from drifting into separate tables by accident."""
    h = _h(app_client)
    e, c = _ev(app_client, h), _a_control(app_client, h)
    app_client.post(f"/api/evidence/{e}/controls", headers=h, json={"control_id": c})
    ctl = app_client.get(f"/api/library/controls/{c}", headers=h).json()
    assert e in [x["id"] for x in ctl["linked_evidence"]]

    assert app_client.delete(f"/api/library/controls/{c}/evidence/{e}",
                             headers=h).status_code == 200
    assert app_client.get(f"/api/evidence/{e}", headers=h).json()["linked_controls"] == []


def test_the_link_row_carries_the_right_tenant(app_client):
    h = _h(app_client)
    e, c = _ev(app_client, h), _a_control(app_client, h)
    app_client.post(f"/api/evidence/{e}/controls", headers=h, json={"control_id": c})
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    from api.database import engine
    with engine.connect() as conn:
        got = conn.execute(sqltext("SELECT tenant_id FROM evidence_controls "
                                   "WHERE evidence_id=:e AND control_id=:c"),
                           {"e": e, "c": c}).scalar()
    assert got == tid


# ---------------------------------------------------------------- file columns + search

def test_list_and_detail_carry_the_file_metadata(app_client):
    h = _h(app_client)
    e = _ev(app_client, h)
    det = app_client.get(f"/api/evidence/{e}", headers=h).json()
    assert det["original_name"] == "proof.pdf"
    assert det["mime_type"] == "application/pdf"
    assert det["size_bytes"] == len(PDF)
    row = next(r for r in app_client.get("/api/evidence", headers=h).json() if r["id"] == e)
    assert row["original_name"] == "proof.pdf" and row["size_bytes"] == len(PDF)


def test_link_evidence_survives_the_files_join(app_client):
    """REGRESSION. The join to `files` must be OUTER. Every artifact seed_demo.py creates is
    medium='LINK' with file_id NULL; an inner join makes the whole seeded vault vanish from
    the list — silent data loss that no happy-path test would notice."""
    h = _h(app_client)
    tid = app_client.get("/api/auth/me", headers=h).json()["tenant_id"]
    lid = str(uuid.uuid4())
    from api.database import engine
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO evidence (id,tenant_id,title,evidence_type,medium,state,"
            "external_url,created_at) VALUES (:i,:t,'Linked artifact','report','LINK',"
            "'FULFILLED','https://example.invalid/x',now_iso())"), {"i": lid, "t": tid})
    rows = app_client.get("/api/evidence", headers=h).json()
    row = next((r for r in rows if r["id"] == lid), None)
    assert row is not None, "a LINK artifact must still appear in the vault"
    assert row["original_name"] is None and row["size_bytes"] is None
    assert row["external_url"] == "https://example.invalid/x"
    assert app_client.get(f"/api/evidence/{lid}", headers=h).json()["medium"] == "LINK"


def test_the_files_join_does_not_clobber_the_evidence_id(app_client):
    """REGRESSION. `files` collides with `evidence` on id, tenant_id and created_at, so
    every joined column has to be .label()ed. Unlabelled, the row's `id` becomes the FILE's
    id and every link in the UI points at nothing."""
    h = _h(app_client)
    e = _ev(app_client, h)
    row = next(r for r in app_client.get("/api/evidence", headers=h).json() if r["id"] == e)
    from api.database import engine
    with engine.connect() as c:
        fid = c.execute(sqltext("SELECT file_id FROM evidence WHERE id=:i"), {"i": e}).scalar()
    assert row["id"] == e and row["id"] != fid
    assert row["file_id"] == fid


def test_search_matches_title_case_insensitively(app_client):
    h = _h(app_client)
    tag = uuid.uuid4().hex[:6]
    e = _ev(app_client, h, title=f"Firewall Ruleset {tag}")
    _ev(app_client, h, title=f"Payroll {tag}")
    got = [r["id"] for r in
           app_client.get(f"/api/evidence?q=firewall+ruleset+{tag}", headers=h).json()]
    assert got == [e]


def test_search_matches_notes_and_survives_a_null_notes_row(app_client):
    """REGRESSION. `notes` is nullable and lower(NULL) LIKE ... is NULL, so without the
    coalesce every artifact with no notes silently drops out of the OR — including ones
    whose title matches perfectly."""
    h = _h(app_client)
    tag = uuid.uuid4().hex[:6]
    plain = _ev(app_client, h, title=f"Zeta {tag}")            # no notes at all
    noted = _ev(app_client, h, title=f"Unrelated {tag}")
    from api.database import engine
    with engine.begin() as c:
        c.execute(sqltext("UPDATE evidence SET notes=:n WHERE id=:i"),
                  {"n": f"mentions zeta {tag}", "i": noted})
    got = {r["id"] for r in app_client.get(f"/api/evidence?q=zeta+{tag}", headers=h).json()}
    assert got == {plain, noted}


def test_search_with_no_match_is_an_empty_list(app_client):
    h = _h(app_client)
    _ev(app_client, h)
    assert app_client.get(f"/api/evidence?q=zzz{uuid.uuid4().hex}", headers=h).json() == []


def test_search_combines_with_the_expiring_filter(app_client):
    h = _h(app_client)
    tag = uuid.uuid4().hex[:6]
    stale = _ev(app_client, h, title=f"Stale {tag}", valid_until="2020-01-01")
    _ev(app_client, h, title=f"Fresh {tag}", valid_until="2099-01-01")
    got = [r["id"] for r in
           app_client.get(f"/api/evidence?q={tag}&expiring=true", headers=h).json()]
    assert got == [stale]


def test_search_does_not_cross_tenants(app_client):
    h = _h(app_client)
    tag = uuid.uuid4().hex[:6]
    mine = _ev(app_client, h, title=f"Shared word {tag}")
    _tid, oh = _other_org(app_client)
    theirs = _ev(app_client, oh, title=f"Shared word {tag}")
    got = [r["id"] for r in app_client.get(f"/api/evidence?q={tag}", headers=h).json()]
    assert got == [mine] and theirs not in got


def test_search_treats_like_wildcards_as_literals_of_the_house_idiom(app_client):
    """Pins CURRENT behaviour: `%` and `_` are not escaped, exactly as the seven other q=
    endpoints behave. Not an endorsement — a deliberate record, so that escaping them later
    is a considered change across all eight rather than an accidental divergence here."""
    h = _h(app_client)
    tag = uuid.uuid4().hex[:6]
    e = _ev(app_client, h, title=f"Alpha Beta {tag}")
    got = [r["id"] for r in app_client.get(f"/api/evidence?q=alpha_beta+{tag}",
                                           headers=h).json()]
    assert got == [e], "an underscore currently matches any single character"


def test_control_search_matches_code_and_statement(app_client):
    """The picker filtered on BOTH code and statement client-side, so a code-only server
    predicate would be a behaviour regression rather than a migration."""
    _tid, oh = _other_org(app_client)
    tag = uuid.uuid4().hex[:6]
    target = _a_control(app_client, oh, code=f"NET {tag}",
                        statement=f"Perimeter firewall rules are reviewed {tag}.")
    _a_control(app_client, oh, code=f"ACC {tag}", statement="Unrelated access statement.")
    all_rows = app_client.get("/api/library/controls", headers=oh).json()
    assert len(all_rows) >= 2

    by_code = app_client.get(f"/api/library/controls?q=net+{tag}", headers=oh).json()
    assert [r["id"] for r in by_code] == [target]
    by_text = app_client.get(f"/api/library/controls?q=perimeter+firewall",
                             headers=oh).json()
    assert [r["id"] for r in by_text] == [target]
    assert len(by_code) < len(all_rows), "search must actually narrow the list"


# ---------------------------------------------------------------- title defaulting

def test_upload_without_a_title_uses_the_filename(app_client):
    h = _h(app_client)
    r = app_client.post("/api/evidence", headers=h, data={"evidence_type": "report"},
                        files={"file": ("VAPT Report Q1.pdf", PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    assert app_client.get(f"/api/evidence/{r.json()['id']}",
                          headers=h).json()["title"] == "VAPT Report Q1"


def test_the_default_strips_only_the_extension(app_client):
    h = _h(app_client)
    r = app_client.post("/api/evidence", headers=h, data={"evidence_type": "report"},
                        files={"file": ("2026.01.15 Firewall.review.pdf", PDF, "application/pdf")})
    assert app_client.get(f"/api/evidence/{r.json()['id']}",
                          headers=h).json()["title"] == "2026.01.15 Firewall.review"


def test_a_blank_title_falls_back_to_the_filename(app_client):
    h = _h(app_client)
    r = app_client.post("/api/evidence", headers=h,
                        data={"title": "   ", "evidence_type": "report"},
                        files={"file": ("fallback.pdf", PDF, "application/pdf")})
    assert app_client.get(f"/api/evidence/{r.json()['id']}",
                          headers=h).json()["title"] == "fallback"


def test_a_dotfile_keeps_its_whole_name(app_client):
    """Python treats a leading dot as a hidden-file marker, not an extension:
    `Path(".pdf").stem` is ".pdf", not "". Documented here because the obvious assumption
    (that this is the case the `or "upload"` fallback exists for) is wrong — that fallback
    covers `file.filename` arriving as None or empty, which Starlette's test client cannot
    produce (it treats a filename-less part as a plain form field, not a file)."""
    h = _h(app_client)
    r = app_client.post("/api/evidence", headers=h, data={"evidence_type": "report"},
                        files={"file": (".pdf", PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    assert app_client.get(f"/api/evidence/{r.json()['id']}",
                          headers=h).json()["title"] == ".pdf"



def test_an_explicit_title_still_wins(app_client):
    h = _h(app_client)
    r = app_client.post("/api/evidence", headers=h,
                        data={"title": "Chosen by hand", "evidence_type": "report"},
                        files={"file": ("ignored.pdf", PDF, "application/pdf")})
    assert app_client.get(f"/api/evidence/{r.json()['id']}",
                          headers=h).json()["title"] == "Chosen by hand"
