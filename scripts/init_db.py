#!/usr/bin/env python3
"""Create the audit_rail schema in PostgreSQL and seed it.

Postgres runs via docker-compose (host port 5433 — Probo holds 5432).
Connection comes from DATABASE_URL / api/config.py.

Seeds:
  - tenant KIAM + two users (admin/member; passwords set by set_dev_password.py)
  - the 16 framework domains with codes (docs/phase2/02-design-handoff-notes.md)
  - the 3 bank checklists as templates → sections → questions, verbatim from
    data/extracted/all_controls.json (470 questions). Canonical controls are
    NOT seeded here — they come from scripts/build_control_library.py.

Usage:
  docker compose up -d
  .venv/bin/python scripts/init_db.py [--force]     # --force resets the schema
"""

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

# Canonical 16-domain framework with short codes — finalized in the Claude-design
# handoff. Asset/Endpoint folds into NI and Email into DP; the framework is
# user-extensible, so new domains can be added anytime.
UNIFIED_DOMAINS = [
    ("GRP", "Governance, Risk & Policy"),
    ("HR",  "HR & People Security"),
    ("AM",  "Access Management"),
    ("NI",  "Network & Infrastructure"),
    ("CS",  "Cloud Security"),
    ("AS",  "Application Security & SDLC"),
    ("DP",  "Data Protection & Privacy"),
    ("LM",  "Logging, Monitoring & SOC"),
    ("VP",  "Vulnerability, Patch & Change"),
    ("IM",  "Incident Management"),
    ("BC",  "BCP, DR & Backup"),
    ("PE",  "Physical & Environmental"),
    ("TP",  "Third-Party & Supply Chain"),
    ("LR",  "Legal & Regulatory"),
    ("BF",  "Business, Financial & Client"),
    ("AI",  "AI/ML Security"),
]

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


def main() -> None:
    force = "--force" in sys.argv
    engine = get_engine()

    with engine.begin() as conn:
        if already_seeded(conn) and not force:
            sys.exit("Schema already exists. Re-run with --force to reset it.")
        if force:
            reset_schema(conn)
        n = apply_schema(conn)
        print(f"  schema applied ({n} statements)")

        # ── tenant + users ───────────────────────────────────────────────────
        tenant_id = uid()
        conn.execute(text(
            "INSERT INTO tenants VALUES (:i,:n,:s,:st,:c)"),
            {"i": tenant_id, "n": "KIAM INTL PVT LTD", "s": "kiam",
             "st": "active", "c": NOW})
        for email, name, role in [
            ("sumit.t@iesglabs.com", "Sumit", "admin"),
            ("intern@kiam.example", "Compliance Intern", "member"),
        ]:
            user_id = uid()
            conn.execute(text(
                "INSERT INTO users (id,email,full_name,auth_provider,is_platform_admin,"
                "status,created_at) VALUES (:i,:e,:f,:a,:p,:s,:c)"),
                {"i": user_id, "e": email, "f": name, "a": "local",
                 "p": 1 if role == "admin" else 0, "s": "invited", "c": NOW})
            conn.execute(text("INSERT INTO tenant_members VALUES (:i,:t,:u,:r,:c)"),
                         {"i": uid(), "t": tenant_id, "u": user_id, "r": role, "c": NOW})

        # ── unified domains ──────────────────────────────────────────────────
        for i, (code, name) in enumerate(UNIFIED_DOMAINS):
            conn.execute(text(
                "INSERT INTO domains (id,tenant_id,code,name,sort_order) "
                "VALUES (:i,:t,:c,:n,:o)"),
                {"i": uid(), "t": tenant_id, "c": code, "n": name, "o": i})

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

    with engine.connect() as conn:
        for table in ["tenants", "users", "domains", "templates",
                      "template_sections", "questions"]:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f"  {table:20s} {n}")
    print(f"\nSeeded {engine.url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
