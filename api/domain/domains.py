"""The canonical 16-domain control framework, seeded into every organisation.

Split out of scripts/init_db.py (P4-S5): that script only ever ran once, at the very
first `init_db.py --force`, so a fresh organisation created through open signup got zero
domains — and `controls.domain_id` is NOT NULL with a composite FK, so "Add control" was a
dead end for every org except the original KIAM install. Mirrors api/vocabularies.py's
seed() shape.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from api.core.database import t

UNIFIED_DOMAINS: list[tuple[str, str]] = [
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


def seed(conn, tenant_id: str) -> int:
    """Give a new organisation the canonical domain framework. Idempotent."""
    d = t("domains")
    existing = set(conn.execute(select(d.c.code).where(d.c.tenant_id == tenant_id)).scalars())
    rows = [
        {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "code": code, "name": name,
         "sort_order": i}
        for i, (code, name) in enumerate(UNIFIED_DOMAINS)
        if code not in existing
    ]
    if rows:
        conn.execute(insert(d), rows)
    return len(rows)
