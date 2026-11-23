"""M2 — control library + crosswalk endpoints (with a small built framework)."""

import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token


def _seed_library(engine, tenant_id):
    """Two domains, two controls, one template with two questions, mapped."""
    now = "2026-07-11T00:00:00Z"
    am_dom, cs_dom = str(uuid.uuid4()), str(uuid.uuid4())
    am_ctl, cs_ctl = str(uuid.uuid4()), str(uuid.uuid4())
    tpl = str(uuid.uuid4())
    q1, q2 = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as c:
        # AM domain already seeded in conftest? insert fresh CS + controls
        c.execute(sqltext("INSERT INTO domains (id,tenant_id,code,name,sort_order) "
                          "VALUES (:i,:t,'CS','Cloud Security',5)"),
                  {"i": cs_dom, "t": tenant_id})
        am_dom = c.execute(sqltext("SELECT id FROM domains WHERE code='AM' AND tenant_id=:t"),
                           {"t": tenant_id}).scalar()
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "recurrence_months,applicability,status,created_at,updated_at) VALUES "
            "(:i,:t,:d,'AM 4.a','Strong password policy','recurring',12,'applicable',"
            "'active',:n,:n)"), {"i": am_ctl, "t": tenant_id, "d": am_dom, "n": now})
        c.execute(sqltext(
            "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
            "applicability,na_justification,reactivation_trigger,stock_response,status,"
            "created_at,updated_at) VALUES (:i,:t,:d,'CS 2.a','Cloud tenant isolation',"
            "'per_audit','not_applicable','on-prem','Cloud adoption','na','active',:n,:n)"),
            {"i": cs_ctl, "t": tenant_id, "d": cs_dom, "n": now})
        c.execute(sqltext("INSERT INTO templates (id,tenant_id,bank_name,title,"
                          "version_label,status,created_at) VALUES "
                          "(:i,:t,'KotakXW','KSL VRA','V3.0','active',:n)"),
                  {"i": tpl, "t": tenant_id, "n": now})
        c.execute(sqltext("INSERT INTO questions (id,template_id,number,text,sort_order) "
                          "VALUES (:i,:tp,'82','Do you enforce strong passwords?',1)"),
                  {"i": q1, "tp": tpl})
        c.execute(sqltext("INSERT INTO questions (id,template_id,number,text,sort_order) "
                          "VALUES (:i,:tp,'41','How is tenant data isolated?',2)"),
                  {"i": q2, "tp": tpl})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,1.0,'suggested',:n)"),
                  {"i": str(uuid.uuid4()), "q": q1, "c": am_ctl, "n": now})
        c.execute(sqltext("INSERT INTO question_control_map (id,question_id,control_id,"
                          "confidence,status,created_at) VALUES (:i,:q,:c,0.67,'suggested',:n)"),
                  {"i": str(uuid.uuid4()), "q": q2, "c": cs_ctl, "n": now})
    return am_ctl, cs_ctl


def test_controls_and_crosswalk(app_client):
    from api.core.database import engine, t
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    am_ctl, cs_ctl = _seed_library(engine, tid)
    tok = token(app_client, "member@kiam.example", "secret2")
    h = {"Authorization": f"Bearer {tok}"}

    # controls list carries domain + mapped counts
    controls = app_client.get("/api/library/controls", headers=h).json()
    by_code = {c["code"]: c for c in controls}
    assert by_code["AM 4.a"]["mapped_count"] == 1
    assert by_code["AM 4.a"]["domain_code"] == "AM"
    assert by_code["CS 2.a"]["applicability"] == "not_applicable"
    assert by_code["CS 2.a"]["reactivation_trigger"] == "Cloud adoption"

    # applicability filter
    dormant = app_client.get("/api/library/controls?applicability=not_applicable",
                             headers=h).json()
    assert {c["code"] for c in dormant} == {"CS 2.a"}

    # control detail shows the mapped bank point
    detail = app_client.get(f"/api/library/controls/{am_ctl}", headers=h).json()
    assert detail["code"] == "AM 4.a"
    assert detail["mapped_points"][0]["number"] == "82"
    assert detail["mapped_points"][0]["bank_name"] == "KotakXW"

    # crosswalk matrix: AM 4.a row has Kotak point 82 in its cell
    xw = app_client.get("/api/library/crosswalk", headers=h).json()
    assert len(xw["columns"]) >= 1
    kotak_col = next(col for col in xw["columns"] if col["bank_name"] == "KotakXW")
    am_row = next(r for r in xw["rows"] if r["code"] == "AM 4.a")
    assert am_row["cells"][kotak_col["id"]] == ["82"]


def test_crosswalk_requires_auth(app_client):
    assert app_client.get("/api/library/crosswalk").status_code == 401


def test_the_unreviewed_mapping_backlog_is_visible_on_the_template_list(app_client):
    """P5-S10. Proposed crosswalk mappings are meant to be confirmed by a human, but the
    review only ever existed inside the import wizard — gated on the in-memory result of an
    import that had just happened. Navigate away and there was no route back, so they piled
    up unseen: 667 of them at ~0.30 average confidence on this install.

    That matters beyond tidiness: an unconfirmed mapping still points a bank's question at a
    control, and that control's stock answer still prefills the audit response. The backlog is
    unverified answers. Surfacing the count is what makes it a visible piece of work.
    """
    from api.core.database import engine
    from sqlalchemy import text as sqltext

    tok = token(app_client, "admin@kiam.example", "secret1")
    h = {"Authorization": f"Bearer {tok}"}
    before = app_client.get("/api/templates", headers=h).json()
    assert before, "the seeded install has templates"
    assert all("unconfirmed_mappings" in t for t in before)

    target = next((t for t in before if t["unconfirmed_mappings"] > 0), None)
    if target is None:                       # pragma: no cover - depends on seed state
        return
    n = target["unconfirmed_mappings"]

    # confirming one drops the count by exactly one
    with engine.connect() as c:
        qid = c.execute(sqltext(
            "SELECT q.id FROM questions q JOIN question_control_map m ON m.question_id = q.id "
            "WHERE q.template_id = :t AND m.status = 'suggested' LIMIT 1"),
            {"t": target["id"]}).scalar()
    r = app_client.post(f"/api/templates/{target['id']}/proposals/confirm", headers=h,
                        json={"decisions": [{"question_id": qid, "action": "confirm"}]})
    assert r.status_code == 200, r.text

    after = next(t for t in app_client.get("/api/templates", headers=h).json()
                 if t["id"] == target["id"])
    assert after["unconfirmed_mappings"] == n - 1


def test_domain_count_excludes_retired_controls(app_client):
    """The sidebar count must match the list: a retired (soft-deleted) control is not in the
    library any more, so it must not inflate the domain's number."""
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    dom, now = str(uuid.uuid4()), "2026-07-11T00:00:00Z"
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO domains (id,tenant_id,code,name,sort_order) "
                          "VALUES (:i,:t,'RCNT','Retired count',90)"), {"i": dom, "t": tid})
        for code, status in [("RCNT 1.a", "active"), ("RCNT 1.b", "active"),
                             ("RCNT 1.c", "retired")]:
            c.execute(sqltext(
                "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
                "applicability,status,created_at,updated_at) VALUES "
                "(:i,:t,:d,:code,'x','per_audit','applicable',:s,:n,:n)"),
                {"i": str(uuid.uuid4()), "t": tid, "d": dom, "code": code,
                 "s": status, "n": now})
    tok = token(app_client, "member@kiam.example", "secret2")
    h = {"Authorization": f"Bearer {tok}"}
    domains = app_client.get("/api/library/domains", headers=h).json()
    mine = next(d for d in domains if d["code"] == "RCNT")
    assert mine["control_count"] == 2
    listed = app_client.get("/api/library/controls?domain_code=RCNT", headers=h).json()
    assert len(listed) == mine["control_count"]


def test_natural_key_orders_numbers_as_numbers():
    from api.core.util import natural_key
    codes = ["DPDP-10", "DPDP-2", "DPDP-11", "DPDP-1"]
    assert sorted(codes, key=natural_key) == ["DPDP-1", "DPDP-2", "DPDP-10", "DPDP-11"]
    assert sorted(["A.5.10", "A.5.9", "A.5.1"], key=natural_key) == ["A.5.1", "A.5.9", "A.5.10"]
    # text and digit runs never compare against each other at the same position
    assert sorted(["X", "X1", "1X", ""], key=natural_key)[0] == ""


def test_controls_list_sorts_codes_numerically_within_a_domain(app_client):
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    dom, now = str(uuid.uuid4()), "2026-07-11T00:00:00Z"
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO domains (id,tenant_id,code,name,sort_order) "
                          "VALUES (:i,:t,'NATS','Natural sort',91)"), {"i": dom, "t": tid})
        for code in ["NATS-10", "NATS-2", "NATS-11", "NATS-1"]:
            c.execute(sqltext(
                "INSERT INTO controls (id,tenant_id,domain_id,code,statement,lifecycle,"
                "applicability,status,created_at,updated_at) VALUES "
                "(:i,:t,:d,:code,'x','per_audit','applicable','active',:n,:n)"),
                {"i": str(uuid.uuid4()), "t": tid, "d": dom, "code": code, "n": now})
    tok = token(app_client, "member@kiam.example", "secret2")
    h = {"Authorization": f"Bearer {tok}"}
    listed = app_client.get("/api/library/controls?domain_code=NATS", headers=h).json()
    assert [r["code"] for r in listed] == ["NATS-1", "NATS-2", "NATS-10", "NATS-11"]
    # the domain's own fields are still there; the internal sort key is not leaked
    assert listed[0]["domain_code"] == "NATS" and "_domain_order" not in listed[0]
