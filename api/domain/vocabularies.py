"""Admin-editable dropdown vocabularies (P4-S3).

Free-text fields invite typos, and a register whose "category" column holds
"Access control", "access-control" and "Acess Control" cannot be grouped or reported on.
These lists therefore live in `lookup_values` — editable per organisation, without a
migration for every new value.

What belongs here: open-ended taxonomies (risk category, vendor category, asset subtype,
data type, incident category).
What does NOT: state machines the code branches on — status, severity, treatment. Those stay
as CHECK constraints so the database keeps enforcing them.

**Classification is a deliberate, narrow exception (P7-S5), and only for `documents`.**
`data_items.classification` is unaffected and stays a closed, DB-enforced set — see
CLASSIFICATIONS below. `documents.classification` prints on a signed PDF/DOCX letterhead, so
widening it was a real trade-off, made because Admin · Masters had no way to manage it at all
(the report that prompted this: "no block for Documents Classification"). Nothing here
branches on `documents.classification`'s value — it is display text, not a state machine —
which is what makes it safe to move into this module despite the general rule above.
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select

from api.core.database import t
from api.core.util import now_iso

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
    # ── P5-S6 ──────────────────────────────────────────────────────────────────
    # The four fields Sumit could not extend. Each was free text or a hardcoded array
    # before this, which is precisely why "Department can't be extended" was reported:
    # People.tsx rendered an <input list="dept-list"> whose options were built from values
    # ALREADY IN USE, so it looked like a fixed dropdown that refused new entries when in
    # fact typing a new value always worked. A real vocabulary makes the affordance honest.
    "department": ("Department", (
        "Compliance", "Information Security", "IT", "Operations", "Finance",
        "Human Resources", "Legal", "Sales", "Engineering", "Field Operations",
    )),
    "position": ("Position / job title", (
        "Director", "Manager", "Team Lead", "Analyst", "Engineer", "Administrator",
        "Consultant", "Auditor", "Field Engineer", "Intern",
    )),
    # Seeded in Title Case, but the column stays free text and `LookupSelect` keeps an
    # off-list value selectable — live data already holds both `report` and `REPORT`, and
    # silently dropping either from an artifact's record would be data loss dressed up as
    # tidying. S6 makes new entries consistent; it does not rewrite history.
    "evidence_type": ("Evidence type", (
        "Certificate", "Report", "Policy document", "Register", "Screenshot",
        "Insurance", "Contract", "Other",
    )),
    "obligation_area": ("Obligation area", (
        "Cyber security", "Data protection", "Outsourcing", "Business continuity",
        "Incident reporting", "Governance", "Audit", "Customer protection",
    )),
    "regulator": ("Regulator", (
        "RBI", "SEBI", "IRDAI", "MeitY", "CERT-In", "NPCI", "DPDP Authority", "Other",
    )),
    # P7-S5. Seeded with the 4 values the CHECK constraint used to enforce, so no existing
    # document is left pointing at a value that isn't offered anywhere. A separate literal
    # tuple from CLASSIFICATIONS below on purpose — that constant validates a DIFFERENT
    # column (data_items.classification, still CHECK-enforced) and must not drift just
    # because this one grows a "RESTRICTED" or similar later.
    "document_classification": ("Document classification", (
        "PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET",
    )),
}

#: Register enums, mirroring the CHECK constraints in db/schema.sql. They live here rather
#: than in the registers router because `api/register_imports.py` needs them too, and that
#: module cannot import the router (the router imports IT). Getting these wrong is not a
#: theoretical risk: the bulk importer was first written against invented spellings
#: ("MITIGATE", "ONBOARDING", "RESTRICTED") and every affected row failed with an opaque
#: constraint error instead of a readable one.
TREATMENTS = ("MITIGATED", "ACCEPTED", "AVOIDED", "TRANSFERRED", "PENDING")
CRITICALITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
CLASSIFICATIONS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET")
TP_STATUSES = ("ACTIVE", "OFFBOARDING", "TERMINATED")
RISK_STATUSES = ("OPEN", "CLOSED")
ASSET_TYPES = ("PHYSICAL", "VIRTUAL")
INCIDENT_STATUSES = ("OPEN", "INVESTIGATING", "RESOLVED", "CLOSED")


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
