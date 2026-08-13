"""Regressions for the defects found during the Playwright/browser testing pass.

Each of these was invisible to the existing API tests, because an API test always sends
the right header, the right endpoint and the right field names — a browser does none of
those for free. See docs/phase3/09-e2e-findings.md.
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text as sqltext

from tests.conftest import token

REPO = Path(__file__).resolve().parent.parent


def _seed_template(engine, tenant_id):
    """A template with two questions. Codes are randomised: the test database is
    session-scoped, so fixed control codes collide with other modules' seeds."""
    ids = {k: str(uuid.uuid4()) for k in ("tpl", "sec", "q1", "q2")}
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,version_label,"
                          "status) VALUES (:i,:t,'ThreadBank','Thread test','v1','active')"),
                  {"i": ids["tpl"], "t": tenant_id})
        c.execute(sqltext("INSERT INTO template_sections (id,template_id,title,sort_order) "
                          "VALUES (:i,:tp,'Governance',1)"),
                  {"i": ids["sec"], "tp": ids["tpl"]})
        for qk, num, txt, order in [("q1", "1", "Do you have an ISP?", 1),
                                    ("q2", "2", "Do you enforce MFA?", 2)]:
            c.execute(sqltext("INSERT INTO questions (id,template_id,section_id,number,text,"
                              "sort_order) VALUES (:i,:tp,:s,:n,:x,:o)"),
                      {"i": ids[qk], "tp": ids["tpl"], "s": ids["sec"],
                       "n": num, "x": txt, "o": order})
    return ids


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _tid(engine):
    with engine.connect() as c:
        return c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()


# ---------------------------------------------------------------- thread isolation

def test_thread_does_not_leak_across_unanswered_questions(app_client):
    """An assessment-level remark used to appear in EVERY unanswered question's thread:
    `response_id == None` renders as `IS NULL`, which is the schema's marker for an
    assessment-level message. And posting from an unanswered question filed the message
    at assessment level, where every other unanswered question would then show it."""
    from api.core.database import engine
    tid = _tid(engine)
    ids = _seed_template(engine, tid)
    h = _h(app_client)
    aid = app_client.post("/api/assessments", headers=h,
                          json={"template_id": ids["tpl"], "title": "Thread leak"}).json()["id"]

    # an assessment-level remark (no question_id)
    assert app_client.post(f"/api/assessments/{aid}/messages", headers=h, json={
        "kind": "remark", "body": "ASSESSMENT LEVEL NOTE"}).status_code == 201

    # neither unanswered question should show it
    for q in (ids["q1"], ids["q2"]):
        d = app_client.get(f"/api/assessments/{aid}/responses/{q}", headers=h).json()
        bodies = [m["body"] for m in d["thread"]]
        assert "ASSESSMENT LEVEL NOTE" not in bodies, f"leaked into question {q}"

    # a message about a specific unanswered question attaches to THAT question only
    assert app_client.post(f"/api/assessments/{aid}/messages", headers=h, json={
        "kind": "ask", "body": "ABOUT Q1 ONLY", "question_id": ids["q1"]}).status_code == 201
    d1 = app_client.get(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=h).json()
    d2 = app_client.get(f"/api/assessments/{aid}/responses/{ids['q2']}", headers=h).json()
    assert "ABOUT Q1 ONLY" in [m["body"] for m in d1["thread"]]
    assert "ABOUT Q1 ONLY" not in [m["body"] for m in d2["thread"]]


# ---------------------------------------------------------------- evidence delete

def test_deleting_referenced_evidence_is_409_not_500(app_client):
    """task_runs.evidence_id is NO ACTION on purpose — the proof behind a completed
    obligation must not vanish. That used to surface as an uncaught IntegrityError (500)."""
    from api.core.database import engine
    tid, h = _tid(engine), _h(app_client)
    ev_id, task_id, run_id = (str(uuid.uuid4()) for _ in range(3))
    with engine.begin() as c:
        member = c.execute(sqltext("SELECT id FROM tenant_members WHERE tenant_id=:t LIMIT 1"),
                           {"t": tid}).scalar()
        c.execute(sqltext(
            "INSERT INTO evidence (id,tenant_id,title,evidence_type,medium,state,external_url,"
            "created_by_member_id) VALUES (:i,:t,'Referenced cert','certificate','LINK',"
            "'FULFILLED','https://example.test/c',:m)"), {"i": ev_id, "t": tid, "m": member})
        c.execute(sqltext(
            "INSERT INTO tasks (id,tenant_id,title,cadence_months,assignee_member_id,status) "
            "VALUES (:i,:t,'Annual cert',12,:m,'active')"),
            {"i": task_id, "t": tid, "m": member})
        c.execute(sqltext(
            "INSERT INTO task_runs (id,task_id,due_at,status,evidence_id) "
            "VALUES (:i,:tk,'2026-01-01','done',:e)"),
            {"i": run_id, "tk": task_id, "e": ev_id})

    r = app_client.delete(f"/api/evidence/{ev_id}", headers=h)
    assert r.status_code == 409, r.text
    assert "referenced" in r.json()["detail"].lower()
    # and it is still there — the delete was refused, not half-applied
    with engine.connect() as c:
        assert c.execute(sqltext("SELECT count(*) FROM evidence WHERE id=:i"),
                         {"i": ev_id}).scalar() == 1


# ---------------------------------------------------------------- strict request bodies

def test_unknown_fields_are_rejected_not_silently_dropped(app_client):
    """Pydantic's default is to discard unrecognised keys, so posting `assessed_on` when
    the field is `assessed_at` returned a cheerful 201 and left the column NULL. In a
    compliance product a silently-dropped date is an integrity problem — it must 422.

    (Both of these are real mistakes made against this API in a single session.)"""
    from api.core.database import engine
    tid, h = _tid(engine), _h(app_client)

    tp = app_client.post("/api/third-parties", headers=h, json={"name": "Strict Vendor"})
    assert tp.status_code == 201, tp.text
    tp_id = tp.json()["id"]

    # wrong names for assessed_at / outcome
    bad = app_client.post(f"/api/third-parties/{tp_id}/assessments", headers=h,
                          json={"assessed_on": "2026-01-01", "result": "PASS"})
    assert bad.status_code == 422, bad.text

    # the right names still work
    good = app_client.post(f"/api/third-parties/{tp_id}/assessments", headers=h,
                           json={"assessed_at": "2026-01-01", "outcome": "PASS"})
    assert good.status_code == 201, good.text

    # …and the value actually landed, rather than being quietly dropped
    with engine.connect() as c:
        row = c.execute(sqltext("SELECT assessed_at, outcome FROM third_party_assessments "
                                "WHERE third_party_id=:t"), {"t": tp_id}).mappings().first()
    assert row["assessed_at"].startswith("2026-01-01") and row["outcome"] == "PASS"

    # obligations: `title`/`kind` instead of `requirement`/`type`
    assert app_client.post("/api/obligations", headers=h,
                           json={"title": "x", "kind": "LEGAL"}).status_code == 422


# ---------------------------------------------------------------- tenant isolation

def test_template_sub_resources_are_tenant_scoped(app_client):
    """`/templates/{id}` checked ownership, but questions / proposals / scoring keyed only
    off the path id — so a member holding another tenant's template UUID could read that
    bank's questions and control mappings. RLS is inert (the app connects as the table
    owner), so this app-level check IS the boundary."""
    from api.core.database import engine
    other_tenant, other_tpl, sec, q = (str(uuid.uuid4()) for _ in range(4))
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO tenants (id,name,slug,status) "
                          "VALUES (:i,'Rival Bank Ltd',:s,'active')"),
                  {"i": other_tenant, "s": f"rival-{other_tenant[:8]}"})
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,version_label,"
                          "status) VALUES (:i,:t,'RivalBank','Confidential checklist','v9',"
                          "'active')"), {"i": other_tpl, "t": other_tenant})
        c.execute(sqltext("INSERT INTO template_sections (id,template_id,title,sort_order) "
                          "VALUES (:i,:tp,'Secret',1)"), {"i": sec, "tp": other_tpl})
        c.execute(sqltext("INSERT INTO questions (id,template_id,section_id,number,text,"
                          "sort_order) VALUES (:i,:tp,:s,'1','SECRET RIVAL QUESTION',1)"),
                  {"i": q, "tp": other_tpl, "s": sec})

    h = _h(app_client)          # a member of the ORIGINAL tenant
    for path in (f"/api/templates/{other_tpl}",
                 f"/api/templates/{other_tpl}/questions",
                 f"/api/templates/{other_tpl}/proposals",
                 f"/api/templates/{other_tpl}/scoring"):
        r = app_client.get(path, headers=h)
        assert r.status_code == 404, f"{path} leaked another tenant's data: {r.text[:200]}"
        assert "SECRET RIVAL QUESTION" not in r.text

    # and writes are refused too
    assert app_client.post(f"/api/templates/{other_tpl}/proposals/confirm", headers=h,
                           json={"confirm_high_confidence": 0.1}).status_code == 404


# ---------------------------------------------------------------- checklist parsing

REAL_XLSX = sorted((REPO / "data").glob("*.xlsx"))


@pytest.mark.skipif(not REAL_XLSX, reason="bank workbooks not present")
@pytest.mark.parametrize("path", REAL_XLSX, ids=lambda p: p.name[:24])
def test_every_committed_bank_workbook_parses_into_real_questions(path):
    """All three shipped workbooks used to fail: the VRA file 400'd (its active sheet is an
    empty 'Sheet2'), and the other two imported 'S.No.' / 'Yes' as the question text."""
    from api.rendering.xlsx_io import parse_checklist
    meta, rows = parse_checklist(path.read_bytes())
    assert len(rows) > 50, f"{path.name}: only {len(rows)} rows"
    sample = [r["text"] for r in rows[:10] if r["text"]]
    assert sample, "no question text extracted"
    # the extracted column must hold questions, not codes or answers
    assert sum(len(t) > 25 for t in sample) >= len(sample) // 2, sample[:3]
    assert not any(t.strip() in ("Yes", "No", "S.No.") for t in sample), sample[:3]


def test_csv_checklists_are_supported():
    """The picker has always advertised `accept=".xlsx,.csv"`; CSV used to die on
    openpyxl with an opaque BadZipFile."""
    from api.rendering.xlsx_io import parse_checklist
    csv_bytes = (b"S.No,Domain,Question\n"
                 b"1,Governance,Do you maintain a documented information security policy?\n"
                 b"2,Access,Do you enforce multi-factor authentication for admins?\n")
    meta, rows = parse_checklist(csv_bytes)
    assert len(rows) == 2
    assert rows[0]["text"].startswith("Do you maintain")


def test_unknown_sheet_name_is_a_clear_error_not_silent_wrong_data():
    """A typo'd sheet used to fall through to the active sheet and import the wrong tab."""
    from api.rendering.xlsx_io import parse_checklist
    csv_bytes = b"Question\nDo you have a documented policy in place for reviews?\n"
    with pytest.raises(ValueError, match="no sheet named"):
        parse_checklist(csv_bytes, sheet="NotARealSheet")


# ---------------------------------------------------------------- dashboard queues

def test_dashboard_surfaces_documents_not_just_legacy_policies(app_client):
    """The review queue read the legacy `policies` table, so no post-pivot Document could
    ever appear in it however overdue. It now reads documents (and any legacy policy that
    has not been migrated), without double-counting migrated ones."""
    from api.core.database import engine
    tid, h = _tid(engine), _h(app_client)
    pid, did = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                          "VALUES (:i,:t,'Doc Owner',:e)"),
                  {"i": pid, "t": tid, "e": f"owner-{uuid.uuid4().hex[:6]}@kiam.example"})
        c.execute(sqltext(
            "INSERT INTO documents (id,tenant_id,title,document_type,classification,write_mode,"
            "owner_person_id,status,next_review_at) VALUES (:i,:t,'Overdue E2E Policy','POLICY',"
            "'INTERNAL','AUTHORED',:o,'ACTIVE','2020-01-01')"),
            {"i": did, "t": tid, "o": pid})

    q = app_client.get("/api/dashboard", headers=h).json()["queues"]
    assert "documents_due" in q
    titles = [d["title"] for d in q["documents_due"]]
    assert "Overdue E2E Policy" in titles
    assert any(d["kind"] == "document" for d in q["documents_due"])
