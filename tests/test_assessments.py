"""M4 — the audit workspace: prefill, responses, threads, findings, guests."""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _seed(engine, tenant_id, suffix=""):
    """A template with 2 questions; q1 mapped to a stock-answered control.

    `suffix` keeps control codes unique: the test database is session-scoped, so a second
    call with the same codes trips UNIQUE (tenant_id, code).
    """
    now = "2026-07-11T00:00:00Z"
    ids = {k: str(uuid.uuid4()) for k in
           ("ctl1", "ctl2", "tpl", "sec", "q1", "q2", "m1", "m2")}
    c1, c2 = f"ASM 1.a{suffix}", f"ASM 2.a{suffix}"
    ids["code1"], ids["code2"] = c1, c2
    with engine.begin() as c:
        dom = c.execute(sqltext("SELECT id FROM domains WHERE code='AM' AND tenant_id=:t"),
                        {"t": tenant_id}).scalar()
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "applicability,stock_response,stock_comment,status,created_at,updated_at) "
            "VALUES (:i,:t,:d,:code,'ISP','one_time','applicable','yes',"
            "'KIAM maintains a documented ISP.','active',:n,:n)"),
            {"i": ids["ctl1"], "t": tenant_id, "d": dom, "n": now, "code": c1})
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "applicability,status,created_at,updated_at) VALUES "
            "(:i,:t,:d,:code,'MFA','one_time','applicable','active',:n,:n)"),
            {"i": ids["ctl2"], "t": tenant_id, "d": dom, "n": now, "code": c2})
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,"
                          "version_label,status,created_at) VALUES "
                          "(:i,:t,'Kotak','KSL VRA','V3.0','active',:n)"),
                  {"i": ids["tpl"], "t": tenant_id, "n": now})
        c.execute(sqltext("INSERT INTO template_sections (id,template_id,title,sort_order) "
                          "VALUES (:i,:tp,'Governance',1)"),
                  {"i": ids["sec"], "tp": ids["tpl"]})
        for qk, num, txt, order in [("q1", "1", "Do you have an ISP?", 1),
                                    ("q2", "2", "Do you enforce MFA?", 2)]:
            c.execute(sqltext("INSERT INTO questions (id,template_id,section_id,number,"
                              "text,sort_order) VALUES (:i,:tp,:s,:n,:x,:o)"),
                      {"i": ids[qk], "tp": ids["tpl"], "s": ids["sec"],
                       "n": num, "x": txt, "o": order})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,1.0,'confirmed',:n)"),
                  {"i": ids["m1"], "q": ids["q1"], "c": ids["ctl1"], "n": now})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,0.8,'suggested',:n)"),
                  {"i": ids["m2"], "q": ids["q2"], "c": ids["ctl2"], "n": now})
    return ids


def test_full_workspace_roundtrip(app_client):
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    ids = _seed(engine, tid)
    mtok = token(app_client, "member@kiam.example", "secret2")
    atok = token(app_client, "admin@kiam.example", "secret1")
    mh = {"Authorization": f"Bearer {mtok}"}
    ah = {"Authorization": f"Bearer {atok}"}

    # create assessment
    r = app_client.post("/api/assessments", headers=mh,
                        json={"template_id": ids["tpl"], "title": "KSL 2026"})
    assert r.status_code == 201
    aid = r.json()["id"]

    # prefill: q1 has a stock answer, q2 does not
    pf = app_client.post(f"/api/assessments/{aid}/prefill", headers=mh).json()
    assert pf["prefilled"] == 1

    # questions grid: q1 answered from stock + mapped control ref
    grid = app_client.get(f"/api/assessments/{aid}/questions", headers=mh).json()
    g = {row["number"]: row for row in grid}
    assert g["1"]["response_value"] == "yes"
    assert g["1"]["workflow_status"] == "answered"
    assert g["1"]["mapped_control"] == "ASM 1.a"
    assert g["2"]["workflow_status"] == "open"

    # NA guard: no justification -> 400; with justification -> ok
    bad = app_client.put(f"/api/assessments/{aid}/responses/{ids['q2']}", headers=mh,
                        json={"response_value": "na"})
    assert bad.status_code == 400
    ok = app_client.put(f"/api/assessments/{aid}/responses/{ids['q2']}", headers=mh,
                       json={"response_value": "na", "na_justification": "No MFA; on-prem."})
    assert ok.status_code == 200

    # a revision is recorded when we change the answer (final = yes -> 100% score)
    app_client.put(f"/api/assessments/{aid}/responses/{ids['q2']}", headers=mh,
                  json={"response_value": "yes", "comment": "reconsidered"})
    detail = app_client.get(f"/api/assessments/{aid}/responses/{ids['q2']}", headers=mh).json()
    assert len(detail["revisions"]) == 2
    assert detail["response"]["response_value"] == "yes"

    # invite an auditor guest -> get a scoped token
    inv = app_client.post(f"/api/assessments/{aid}/guests", headers=ah, json={
        "email": "auditor@pwc.example", "full_name": "A. Mehta", "firm": "PwC",
        "expires_at": "2026-12-31"})
    assert inv.status_code == 201
    gtok = inv.json()["access_token"]
    gh = {"Authorization": f"Bearer {gtok}"}

    # guest can read the grid...
    assert app_client.get(f"/api/assessments/{aid}/questions", headers=gh).status_code == 200
    # ...but cannot edit vendor answers (member-only endpoint)
    assert app_client.put(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=gh,
                        json={"response_value": "no"}).status_code == 403
    # guest asks a question on the thread -> response goes ask_pending
    m = app_client.post(f"/api/assessments/{aid}/messages", headers=gh, json={
        "kind": "ask", "body": "Please share the ISP.", "question_id": ids["q1"]})
    assert m.status_code == 201
    d1 = app_client.get(f"/api/assessments/{aid}/responses/{ids['q1']}", headers=mh).json()
    assert d1["response"]["workflow_status"] == "ask_pending"
    assert d1["thread"][0]["author_kind"] == "auditor"

    # guest raises a finding (Likelihood 2 x Impact 3 = 6 -> High)
    f = app_client.post(f"/api/assessments/{aid}/findings", headers=gh, json={
        "title": "ISP not evidenced", "response_id": d1["response"]["id"],
        "likelihood": 2, "impact": 3}).json()
    assert f["risk_score"] == 6 and f["risk_rating"] == "high"

    # an open High finding drops the predicted verdict to Conditional
    det = app_client.get(f"/api/assessments/{aid}", headers=mh).json()
    assert det["open_high_findings"] == 1
    assert det["predicted_verdict"] == "Conditional"

    # revoke the guest -> access denied
    gid = inv.json()["guest_id"]
    assert app_client.delete(f"/api/assessments/{aid}/guests/{gid}",
                            headers=ah).status_code == 204
    assert app_client.get(f"/api/assessments/{aid}/questions", headers=gh).status_code == 403


def test_assessment_requires_auth(app_client):
    assert app_client.get("/api/assessments").status_code == 401


# ────────────────────────────────────────────────────────────── P4-S5: rejected mappings

def test_a_rejected_mapping_never_prefills_or_shows_as_the_control(app_client):
    """Rejecting a proposed mapping means "this bank point is not about that control".

    Before P4-S5, `_best_controls` sorted 'confirmed' ahead of everything else but never
    EXCLUDED 'rejected' — so a question whose only mapping had been rejected still showed
    that control in the grid and prefilled its stock answer, putting an answer the reviewer
    had explicitly refused in front of a bank auditor.
    """
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    ids = _seed(engine, tid, suffix="-rej")

    # reject q1's only mapping, and give q2's control a stock answer so the *other* half
    # of the behaviour (a live mapping still prefills) stays proven in the same test
    with engine.begin() as c:
        c.execute(sqltext("UPDATE question_control_map SET status='rejected' WHERE id=:m"),
                  {"m": ids["m1"]})
        c.execute(sqltext("UPDATE controls SET stock_response='no', "
                          "stock_comment='MFA not yet enforced.' WHERE id=:c"),
                  {"c": ids["ctl2"]})

    h = {"Authorization": f"Bearer {token(app_client, 'member@kiam.example', 'secret2')}"}
    aid = app_client.post("/api/assessments", headers=h,
                          json={"template_id": ids["tpl"], "title": "Rejected-map audit"}
                          ).json()["id"]

    pf = app_client.post(f"/api/assessments/{aid}/prefill", headers=h).json()
    assert pf["prefilled"] == 1, "only q2 should prefill; q1's mapping was rejected"

    grid = {row["number"]: row for row in
            app_client.get(f"/api/assessments/{aid}/questions", headers=h).json()}
    assert grid["1"]["response_value"] is None
    assert grid["1"]["workflow_status"] == "open"
    assert grid["1"]["mapped_control"] is None, "a rejected control must not be shown as mapped"
    # the live mapping still works
    assert grid["2"]["response_value"] == "no"
    assert grid["2"]["mapped_control"] == ids["code2"]
