#!/usr/bin/env python3
"""Create the Audit Rail schema in PostgreSQL, and optionally seed the dev tenant.

Postgres runs via docker-compose (host port 5434 — Probo holds 5432, emp_erp_mcp 5433).
Connection comes from DATABASE_URL / api/config.py.

Two modes:

  --force            reset the schema, then seed the KIAM dev tenant:
                       • tenant KIAM + two users (passwords set by set_dev_password.py)
                       • system roles, vocabularies, the 16 domains, 3 frameworks
                       • the 3 bank checklists as templates → sections → questions,
                         verbatim from data/extracted/all_controls.json (470 questions)
                     Canonical controls are NOT seeded here — they come from
                     scripts/build_control_library.py.

  --force --blank    reset the schema and seed NOTHING. Zero tenants, zero users. The
                     portal is then walked from /signup exactly as a new customer walks
                     it — `auth._create_org()` gives every new organisation its own
                     roles, vocabularies, 16 domains, 95 controls and 3 frameworks, so an
                     empty database is genuinely usable rather than merely startable.

`--blank` deliberately does NOT imply `--force`: destroying an existing database still
means typing the word, and there is exactly one word that does it.

Usage:
  docker compose up -d
  .venv/bin/python scripts/init_db.py --force            # dev tenant
  .venv/bin/python scripts/init_db.py --force --blank    # nothing at all
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from _db import apply_schema, get_engine, reset_schema  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONTROLS_JSON = REPO / "data" / "extracted" / "all_controls.json"

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Canonical 16-domain framework — moved to api/domains.py (P4-S5), which
# api.routers.auth also seeds at open signup; a fresh organisation used to get zero
# domains, and controls.domain_id (NOT NULL) made "Add control" a dead end.
# Imported lazily where used, below — api.database reflects the schema at import time,
# which must happen AFTER apply_schema(), not at module load.

TEMPLATE_META = {
    "VRA_v1.2": ("Unspecified Bank", "VRA Assessment Checklist", "v1.2",
                 ["yes", "no", "na"]),
    "AnnexureC_v2.7": ("Unspecified Bank (Annexure C)",
                       "Pre-Onboarding Assessment Questionnaire — Annexure C", "v2.7",
                       ["yes", "no", "na"]),
    "KSL_v3.0": ("Kotak Securities", "KSL IS Vendor Risk Assessment", "V3.0",
                 ["yes", "partial", "no", "na"]),
}


def uid() -> str:
    return str(uuid.uuid4())


def already_seeded(conn) -> bool:
    row = conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='tenants')")).scalar()
    return bool(row)


def seed_kiam(conn) -> None:
    """The dev tenant. Everything in here is one customer's data, not product data —
    which is exactly why `--blank` skips the whole function rather than trying to
    subtract pieces of it."""
    # Checked BEFORE anything is written, because `data/` is gitignored and a fresh clone
    # does not have this file. Reading it 90 lines further down meant `setup.sh` died with
    # a bare FileNotFoundError and no explanation.
    #
    # Failing HERE also makes the failure harmless rather than merely legible: this runs
    # inside the caller's `engine.begin()`, Postgres DDL is transactional, so `sys.exit`
    # rolls the DROP/CREATE SCHEMA back with it. Verified — an existing database still has
    # all its rows after a run that hits this line.
    if not CONTROLS_JSON.exists():
        sys.exit(
            f"Cannot seed: {CONTROLS_JSON.relative_to(REPO)} is missing.\n"
            "  That file is gitignored, so a fresh clone does not have it. Either copy it\n"
            "  in, or run with --blank for a schema-only database and sign up at /signup.")

    # ── tenant + users ───────────────────────────────────────────────────
    tenant_id = uid()
    # P4: an organisation is keyed by its GSTIN, and owned by a Super Admin.
    from api.gstin import checksum  # noqa: E402
    gst = "27AAPFU0939F1Z" + checksum("27AAPFU0939F1Z")
    conn.execute(text(
        "INSERT INTO tenants (id,name,slug,gst_number,status,created_at) "
        "VALUES (:i,:n,:s,:g,:st,:c)"),
        {"i": tenant_id, "n": "KIAM INTL PVT LTD", "s": "kiam", "g": gst,
         "st": "active", "c": NOW})
    first_admin = None
    for email, name, role in [
        ("sumit.t@iesglabs.com", "Sumit", "admin"),
        ("intern@kiam.example", "Compliance Intern", "member"),
    ]:
        user_id = uid()
        if role == "admin" and first_admin is None:
            first_admin = user_id
        conn.execute(text(
            "INSERT INTO users (id,email,full_name,auth_provider,is_platform_admin,"
            "status,created_at) VALUES (:i,:e,:f,:a,:p,:s,:c)"),
            {"i": user_id, "e": email, "f": name, "a": "local",
             "p": 1 if role == "admin" else 0, "s": "invited", "c": NOW})
        conn.execute(text("INSERT INTO tenant_members (id,tenant_id,user_id,role,"
                          "created_at) VALUES (:i,:t,:u,:r,:c)"),
                     {"i": uid(), "t": tenant_id, "u": user_id, "r": role, "c": NOW})
    conn.execute(text("UPDATE tenants SET super_admin_user_id=:u WHERE id=:t"),
                 {"u": first_admin, "t": tenant_id})

    # P4-S2: seed the system roles and point each membership at the right one.
    # Raw SQL on purpose. seed_roles() goes through the reflected metadata, and
    # metadata.reflect() opens a SECOND connection — which blocks forever behind the
    # schema locks this very transaction is still holding (DROP/CREATE SCHEMA above).
    from api.permissions import LEGACY_ROLE_MAP, SYSTEM_ROLES  # noqa: E402
    by_name = {}
    for role_name, spec in SYSTEM_ROLES.items():
        rid = uid()
        by_name[role_name] = rid
        conn.execute(text("INSERT INTO roles (id,tenant_id,name,description,is_system,"
                          "created_at) VALUES (:i,:t,:n,:d,1,:c)"),
                     {"i": rid, "t": tenant_id, "n": role_name,
                      "d": spec["description"], "c": NOW})
        for module, action in sorted(spec["permissions"]()):
            conn.execute(text("INSERT INTO role_permissions (role_id,module,action) "
                              "VALUES (:r,:m,:a)"),
                         {"r": rid, "m": module, "a": action})
    for legacy, role_name in LEGACY_ROLE_MAP.items():
        conn.execute(text("UPDATE tenant_members SET role_id=:r "
                          "WHERE tenant_id=:t AND role=:legacy"),
                     {"r": by_name[role_name], "t": tenant_id, "legacy": legacy})

    # P4-S3: starting dropdown vocabularies (raw SQL, same reflection caveat as above).
    from api.vocabularies import KINDS  # noqa: E402
    for kind, (_label, defaults) in KINDS.items():
        for i, value in enumerate(defaults):
            conn.execute(text("INSERT INTO lookup_values (id,tenant_id,kind,value,"
                              "sort_order,is_active,created_at) "
                              "VALUES (:i,:t,:k,:v,:o,1,:c)"),
                         {"i": uid(), "t": tenant_id, "k": kind, "v": value,
                          "o": i, "c": NOW})

    # ── unified domains ──────────────────────────────────────────────────
    from api.domains import UNIFIED_DOMAINS  # noqa: E402
    for i, (code, name) in enumerate(UNIFIED_DOMAINS):
        conn.execute(text(
            "INSERT INTO domains (id,tenant_id,code,name,sort_order) "
            "VALUES (:i,:t,:c,:n,:o)"),
            {"i": uid(), "t": tenant_id, "c": code, "n": name, "o": i})

    # ── certification frameworks + their clauses (P5-S9) ─────────────────
    # Raw SQL like everything else in this function: the reflected metadata is not
    # available while the schema is still being built in this same transaction.
    from api.frameworks import BASELINE  # noqa: E402
    for code, (fname, version, clauses) in BASELINE.items():
        fid = uid()
        conn.execute(text(
            "INSERT INTO frameworks (id,tenant_id,code,name,version,source) "
            "VALUES (:i,:t,:c,:n,:v,'AUTHORED')"),
            {"i": fid, "t": tenant_id, "c": code, "n": fname, "v": version})
        for i, (ref, title) in enumerate(clauses):
            conn.execute(text(
                "INSERT INTO framework_clauses (id,tenant_id,framework_id,ref,title,"
                "sort_order) VALUES (:i,:t,:f,:r,:ti,:o)"),
                {"i": uid(), "t": tenant_id, "f": fid, "r": ref, "ti": title, "o": i})

    # ── the 3 checklists → templates / sections / questions ─────────────
    records = json.loads(CONTROLS_JSON.read_text())
    by_source: dict = {}
    for rec in records:
        by_source.setdefault(rec["source"], []).append(rec)

    for source, recs in by_source.items():
        bank, title, version, scale = TEMPLATE_META[source]
        template_id = uid()
        conn.execute(text(
            "INSERT INTO templates (id,tenant_id,bank_name,title,version_label,"
            "status,notes,created_at) VALUES (:i,:t,:b,:ti,:v,:s,:n,:c)"),
            {"i": template_id, "t": tenant_id, "b": bank, "ti": title,
             "v": version, "s": "active",
             "n": f"Seeded from {recs[0]['source_file']}", "c": NOW})

        sections: dict = {}
        sort = 0

        def section_for(domain: str, sub: str) -> str:
            nonlocal sort
            if (domain, "") not in sections:
                sid = uid()
                sort += 1
                conn.execute(text(
                    "INSERT INTO template_sections (id,template_id,title,sort_order)"
                    " VALUES (:i,:t,:ti,:o)"),
                    {"i": sid, "t": template_id, "ti": domain, "o": sort})
                sections[(domain, "")] = sid
            if not sub:
                return sections[(domain, "")]
            if (domain, sub) not in sections:
                sid = uid()
                sort += 1
                conn.execute(text(
                    "INSERT INTO template_sections (id,template_id,parent_id,title,"
                    "sort_order) VALUES (:i,:t,:p,:ti,:o)"),
                    {"i": sid, "t": template_id, "p": sections[(domain, "")],
                     "ti": sub, "o": sort})
                sections[(domain, sub)] = sid
            return sections[(domain, sub)]

        for i, rec in enumerate(recs):
            conn.execute(text(
                "INSERT INTO questions (id,template_id,section_id,number,text,"
                "rationale,testing_procedure,evidence_mandatory,classification,"
                "response_scale,sort_order) "
                "VALUES (:i,:t,:s,:n,:x,:r,:tp,:em,:cl,:rs,:o)"),
                {"i": uid(), "t": template_id,
                 "s": section_for(rec["domain"], rec.get("sub_domain", "")),
                 "n": rec.get("control_no", ""), "x": rec["question"],
                 "r": rec.get("rationale") or None,
                 "tp": rec.get("testing_procedure") or None,
                 "em": 1 if rec.get("mandatory_evidence") == "Yes" else 0,
                 "cl": rec.get("classification") or None,
                 "rs": json.dumps(scale), "o": i})

BLANK_NEXT_STEPS = """
Next:
  bash start.sh                     then open http://127.0.0.1:3002
  The app bounces you to /login — click "Create an organisation".

  Your first signup becomes its own Super Admin, and seeds its own roles,
  vocabularies, 16 domains, 95 controls and 3 frameworks. It gets NO assessment
  templates: import a checklist at Audits -> Import. That is by design.

Do NOT run these against a blank database:
  scripts/build_control_library.py  picks `SELECT id FROM tenants LIMIT 1` and then
                                    DELETEs that tenant's controls — it would gut the
                                    organisation you just signed up.
  scripts/set_dev_password.py       there are no users to set a password on, and it
                                    sets ONE shared password on every local account.
  scripts/seed_demo.py              adds [demo] rows that clutter a clean walkthrough.

The blob vault is untouched by this script. To clear it too:
  .venv/bin/python scripts/reset_vault.py         # dry run first
"""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Create the Audit Rail schema, and optionally seed the dev tenant.")
    ap.add_argument("--force", action="store_true",
                    help="drop and recreate the public schema first (destructive)")
    ap.add_argument("--blank", action="store_true",
                    help="seed nothing: zero tenants, zero users. Walk the portal from "
                         "/signup. Still needs --force on a database that has tables.")
    args = ap.parse_args()
    engine = get_engine()

    with engine.begin() as conn:
        if already_seeded(conn) and not args.force:
            sys.exit("Schema already exists. Re-run with --force to reset it.")
        if args.force:
            reset_schema(conn)
        n = apply_schema(conn)
        print(f"  schema applied ({n} statements)")
        if not args.blank:
            seed_kiam(conn)

    url = engine.url.render_as_string(hide_password=True)
    with engine.connect() as conn:
        if args.blank:
            tables = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public'")).scalar()
            tenants = conn.execute(text("SELECT COUNT(*) FROM tenants")).scalar()
            users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            print(f"  blank database — {tenants} tenants, {users} users, {tables} tables")
            print(f"\nReady: {url}")
            print(BLANK_NEXT_STEPS)
            return
        for table in ["tenants", "users", "domains", "templates",
                      "template_sections", "questions"]:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f"  {table:20s} {n}")
    print(f"\nSeeded {url}")


if __name__ == "__main__":
    main()
