"""Admin-editable dropdown vocabularies (P4-S3).

Free-text fields invite typos, and a register whose "category" column holds
"Access control", "access-control" and "Acess Control" cannot be grouped or reported on.
These lists therefore live in `lookup_values` — editable per organisation, without a
migration for every new value.

What belongs here: open-ended taxonomies (risk category, vendor category, asset subtype,
data type, incident category).
What does NOT: state machines the code branches on — status, severity, classification,
treatment. Those stay as CHECK constraints so the database keeps enforcing them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from api.database import t
from api.util import now_iso

#: kind -> (human label, seeded defaults)
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "risk_category": ("Risk category", (
        "Access control", "Data protection", "Business continuity", "Third party",
        "Physical security", "Network security", "Application security", "Compliance",
        "People & training", "Operations",
    )),
    "third_party_category": ("Third-party category", (
        "Cloud / hosting", "Software vendor", "Data centre", "Payment processor",
        "Managed service provider", "Professional services", "Connectivity",
        "Hardware supplier", "Staffing", "Other",
    )),
    "asset_subtype": ("Asset subtype", (
        "Server", "Workstation", "Laptop", "Network device", "Mobile device",
        "Virtual machine", "Container", "Cloud service", "SaaS subscription",
        "Database instance", "Storage", "Peripheral",
    )),
    "data_type": ("Data type", (
        "Database", "Object storage", "Application", "API", "File server",
        "Cloud storage", "Backup", "Email", "Physical records",
    )),
    "incident_category": ("Incident category", (
        "Availability", "Data breach", "Malware", "Phishing", "Unauthorised access",
        "Configuration error", "Hardware failure", "Third-party outage", "Other",
    )),
}

#: Document types, mirroring the CHECK on `documents.document_type` (db/schema.sql).
#: NOT a lookup_values kind: the database enforces this set, so an admin cannot be allowed
#: to add to it. It lives here so the router can reject a bad value with a 400 instead of a
#: 500 CheckViolation, and so the UI can render the list rather than hardcode a stale copy —
#: which is exactly how "STANDARD" (never a valid value) reached production.
#: `tests/test_documents.py` asserts this equals the live constraint.
DOCUMENT_TYPES: tuple[str, ...] = (
    "GOVERNANCE", "POLICY", "PROCEDURE", "PLAN",
    "REGISTER", "RECORD", "REPORT", "TEMPLATE", "SOA",
)


def seed(conn, tenant_id: str) -> int:
    """Give a new organisation a sensible starting vocabulary. Idempotent."""
    lv = t("lookup_values")
    existing = {(r["kind"], r["value"]) for r in conn.execute(
        select(lv.c.kind, lv.c.value).where(lv.c.tenant_id == tenant_id)).mappings()}
    rows = [
        {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "kind": kind, "value": value,
         "sort_order": i, "is_active": 1, "created_at": now_iso()}
        for kind, (_label, defaults) in KINDS.items()
        for i, value in enumerate(defaults)
        if (kind, value) not in existing
    ]
    if rows:
        conn.execute(insert(lv), rows)
    return len(rows)
