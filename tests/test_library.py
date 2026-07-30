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
    from api.database import engine, t
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
