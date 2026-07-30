#!/usr/bin/env python3
"""Build a deterministic `audit_rail_e2e` database for the Playwright suite.

Why a separate database: the browser suite mutates data on every run. Pointing it
at the dev DB would both pollute what you're clicking through and make assertions
drift as rows accumulate (exactly what bit the API tests until they started
isolating with unique names).

Strategy — reuse the real scripts rather than duplicating their logic, so this
also *exercises* them:
    init_db.py --force   -> schema + tenant/users/domains/templates/questions
    set_dev_password.py  -> seeded users are `invited` with no password otherwise
    build_control_library.py -> the 95 controls + bank crosswalk (init omits these)
    seed_demo.py         -> dashboard tasks/evidence/policies
…then a fixture layer below with STABLE, greppable names ("E2E …") that specs can
assert against without guessing at demo data.

Usage:  .venv/bin/python scripts/seed_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import create_engine, text  # noqa: E402

E2E_DB = "audit_rail_e2e"
ADMIN_URL = "postgresql+psycopg://audit:audit@localhost:5434/audit_rail"
E2E_URL = f"postgresql+psycopg://audit:audit@localhost:5434/{E2E_DB}"

PY = str(REPO / ".venv" / "bin" / "python")


def ensure_database() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as c:
        exists = c.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"),
                           {"d": E2E_DB}).scalar()
        if not exists:
            c.execute(text(f'CREATE DATABASE "{E2E_DB}"'))
            print(f"  created database {E2E_DB}")
        else:
            print(f"  database {E2E_DB} already exists (will be reset)")
    admin.dispose()


def run(script: str, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": E2E_URL, "SCHEDULER_ENABLED": "false"}
    r = subprocess.run([PY, str(REPO / "scripts" / script), *args],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ✗ {script} failed:\n{r.stdout}\n{r.stderr}")
        sys.exit(1)
    print(f"  ✓ {script}")


def fixtures() -> None:
    """Stable rows the specs can rely on. Idempotent: cleared and rebuilt each run."""
    engine = create_engine(E2E_URL, future=True)
    with engine.begin() as c:
        tid = c.execute(text("SELECT id FROM tenants LIMIT 1")).scalar()
        member = c.execute(text("SELECT id FROM tenant_members LIMIT 1")).scalar()

        # ---- people (E2E Engineer has no login — the D-SIGN premise) ----
        people = {}
        for name, dept, email in [
            ("E2E Owner", "Compliance", "e2e.owner@kiam.example"),
            ("E2E Engineer", "Field Ops", "e2e.engineer@kiam.example"),
            ("E2E Approver", "Compliance", "e2e.approver@kiam.example"),
        ]:
            pid = str(uuid.uuid4())
            c.execute(text("INSERT INTO people (id,tenant_id,full_name,email,department) "
                           "VALUES (:i,:t,:n,:e,:d)"),
                      {"i": pid, "t": tid, "n": name, "e": email, "d": dept})
            people[name] = pid

        # ---- registers: one risk linked to a control, one asset, one data item ----
        control_id = c.execute(text("SELECT id FROM controls ORDER BY code LIMIT 1")).scalar()
        risk_id = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO risks (id,tenant_id,reference,title,category,owner_person_id,"
            "inherent_likelihood,inherent_impact,residual_likelihood,residual_impact,"
            "treatment,status) VALUES (:i,:t,'E2E-R1','E2E Unauthorised CMS access',"
            "'Access control',:o,3,3,1,3,'MITIGATED','OPEN')"),
            {"i": risk_id, "t": tid, "o": people["E2E Owner"]})
        if control_id:
            c.execute(text("INSERT INTO risk_links (id,tenant_id,risk_id,target_kind,control_id) "
                           "VALUES (:i,:t,:r,'CONTROL',:c)"),
                      {"i": str(uuid.uuid4()), "t": tid, "r": risk_id, "c": control_id})
        c.execute(text(
            "INSERT INTO data_items (id,tenant_id,name,classification,owner_person_id) "
            "VALUES (:i,:t,'E2E Bank customer data','CONFIDENTIAL',:o)"),
            {"i": str(uuid.uuid4()), "t": tid, "o": people["E2E Owner"]})
        c.execute(text(
            "INSERT INTO assets (id,tenant_id,name,asset_type,criticality,owner_person_id,"
            "data_types_stored) VALUES (:i,:t,'E2E CMS Server','VIRTUAL','CRITICAL',:o,"
            ":d)"),
            {"i": str(uuid.uuid4()), "t": tid, "o": people["E2E Owner"],
             "d": ["E2E Bank customer data"]})

        # ---- third party with a 4th-party child + an expiring assessment ----
        tp = str(uuid.uuid4()); tp_child = str(uuid.uuid4())
        c.execute(text("INSERT INTO third_parties (id,tenant_id,name,criticality) "
                       "VALUES (:i,:t,'E2E CloudCo','HIGH')"), {"i": tp, "t": tid})
        c.execute(text("INSERT INTO third_parties (id,tenant_id,name,parent_third_party_id) "
                       "VALUES (:i,:t,'E2E DataCentre Ltd',:p)"),
                  {"i": tp_child, "t": tid, "p": tp})
        c.execute(text(
            "INSERT INTO third_party_assessments (id,tenant_id,third_party_id,assessed_at,"
            "expires_at,outcome) VALUES (:i,:t,:tp,"
            "to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD')::iso_ts,"
            "to_char((now() + interval '20 days') AT TIME ZONE 'utc','YYYY-MM-DD')::iso_ts,"
            "'PASS')"), {"i": str(uuid.uuid4()), "t": tid, "tp": tp})

        # ---- obligation (RBI) linked to the same control ----
        ob = str(uuid.uuid4())
        c.execute(text("INSERT INTO obligations (id,tenant_id,requirement,regulator,type,status) "
                       "VALUES (:i,:t,'E2E RBI outsourcing due diligence','RBI','LEGAL',"
                       "'PARTIALLY_COMPLIANT')"), {"i": ob, "t": tid})
        if control_id:
            c.execute(text("INSERT INTO control_obligation_map (tenant_id,control_id,obligation_id) "
                           "VALUES (:t,:c,:o)"),
                      {"t": tid, "c": control_id, "o": ob})

        # ---- incident (open, no RCA yet — specs assert the close-gate) ----
        c.execute(text("INSERT INTO incidents (id,tenant_id,title,severity,status) "
                       "VALUES (:i,:t,'E2E CMS outage','HIGH','OPEN')"),
                  {"i": str(uuid.uuid4()), "t": tid})

        # ---- a PUBLISHED document, so attestation/coverage specs have a target ----
        doc = str(uuid.uuid4()); ver = str(uuid.uuid4()); appr = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO documents (id,tenant_id,title,document_type,classification,write_mode,"
            "owner_person_id,review_cadence_months,status) VALUES (:i,:t,"
            "'E2E Acceptable Use Policy','POLICY','INTERNAL','AUTHORED',:o,12,'ACTIVE')"),
            {"i": doc, "t": tid, "o": people["E2E Owner"]})
        c.execute(text(
            "INSERT INTO document_versions (id,tenant_id,document_id,major,minor,content,status,"
            "created_by_member_id) VALUES (:i,:t,:d,1,0,"
            "'# E2E Acceptable Use\n\nLock your screen. Report incidents promptly.','DRAFT',:m)"),
            {"i": ver, "t": tid, "d": doc, "m": member})
        c.execute(text("INSERT INTO document_approvals (id,tenant_id,document_version_id,"
                       "threshold_required,status) VALUES (:i,:t,:v,1,'APPROVED')"),
                  {"i": appr, "t": tid, "v": ver})
        c.execute(text("INSERT INTO document_approval_decisions (id,tenant_id,approval_id,"
                       "approver_person_id,state,decided_at) "
                       "VALUES (:i,:t,:a,:p,'APPROVED',now_iso())"),
                  {"i": str(uuid.uuid4()), "t": tid, "a": appr, "p": people["E2E Approver"]})
        c.execute(text("UPDATE document_versions SET status='PUBLISHED', "
                       "published_at=now_iso() WHERE id=:v"), {"v": ver})
        c.execute(text("UPDATE documents SET current_published_version_id=:v WHERE id=:d"),
                  {"v": ver, "d": doc})
        # audience: the whole Field Ops department must attest
        c.execute(text("INSERT INTO document_audiences (id,tenant_id,document_id,rule,value) "
                       "VALUES (:i,:t,:d,'DEPARTMENT','Field Ops')"),
                  {"i": str(uuid.uuid4()), "t": tid, "d": doc})

    engine.dispose()
    print("  ✓ e2e fixtures (people, registers, third parties, obligation, incident, published doc)")


def main() -> None:
    print(f"Seeding {E2E_DB} …")
    ensure_database()
    run("init_db.py", "--force")
    run("set_dev_password.py")
    run("build_control_library.py")
    run("seed_demo.py")
    fixtures()
    print(f"\nReady: {E2E_URL.replace(':audit@', ':***@')}")


if __name__ == "__main__":
    main()
