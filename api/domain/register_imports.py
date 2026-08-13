"""Per-register import specs — the one place that says what a spreadsheet column means.

Each spec drives three things at once, which is the point: the downloadable template, the
column-mapping UI, and the row builder. Keeping them in one structure is what stops the
template offering a column the importer ignores, or the UI naming a field the table does not
have — a class of drift that is invisible until a customer's import silently drops data.

`build` receives the mapped row and an `importer.Resolver`, and returns the values to insert.
Raising `RowError` from it fails exactly that row; everything else in the file still lands.
"""

from __future__ import annotations

from api.domain import importer, vocabularies
from api.domain.importer import RowError, one_of, require

CRITICALITY = vocabularies.CRITICALITIES
CLASSIFICATION = vocabularies.CLASSIFICATIONS


def _col(key, label, help="", required=False):
    return {"key": key, "label": label, "help": help, "required": required}


def _score(mapped: dict, field: str, what: str) -> int | None:
    """1–5 likelihood/impact. Blank is fine — a half-scored risk is still worth importing,
    and forcing a score would push people to invent one."""
    raw = (mapped.get(field) or "").strip() if mapped.get(field) else ""
    if not raw:
        return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        raise RowError(f"{what} must be a whole number from 1 to 5 (got {raw!r})") from None
    if not 1 <= n <= 5:
        raise RowError(f"{what} must be from 1 to 5 (got {n})")
    return n


# ──────────────────────────────────────────────────────────── risks

def _build_risk(m: dict, r: importer.Resolver) -> dict:
    return {
        "title": require(m, "title", "Title"),
        "reference": m.get("reference") or None,
        "description": m.get("description") or None,
        "category": m.get("category") or None,
        "owner_person_id": r.person(m.get("owner")),
        "inherent_likelihood": _score(m, "inherent_likelihood", "Inherent likelihood"),
        "inherent_impact": _score(m, "inherent_impact", "Inherent impact"),
        "residual_likelihood": _score(m, "residual_likelihood", "Residual likelihood"),
        "residual_impact": _score(m, "residual_impact", "Residual impact"),
        "treatment": one_of(m, "treatment", vocabularies.TREATMENTS,
                            "Treatment") or "MITIGATED",
        "status": one_of(m, "status", vocabularies.RISK_STATUSES, "Status") or "OPEN",
    }


# ──────────────────────────────────────────────────────────── assets

def _build_asset(m: dict, r: importer.Resolver) -> dict:
    return {
        "name": require(m, "name", "Name"),
        "description": m.get("description") or None,
        "asset_type": one_of(m, "asset_type", vocabularies.ASSET_TYPES, "Type") or "VIRTUAL",
        "owner_person_id": r.person(m.get("owner")),
        "criticality": one_of(m, "criticality", CRITICALITY, "Criticality"),
        "location": m.get("location") or None,
        "subtype": m.get("subtype") or None,
        "vendor_third_party_id": r.vendor(m.get("vendor")),
    }


# ──────────────────────────────────────────────────────────── data inventory

def _build_data_item(m: dict, r: importer.Resolver) -> dict:
    return {
        "name": require(m, "name", "Name"),
        "description": m.get("description") or None,
        "owner_person_id": r.person(m.get("owner")),
        "classification": one_of(m, "classification", CLASSIFICATION,
                                 "Classification") or "INTERNAL",
        "data_type": m.get("data_type") or None,
        "retention_note": m.get("retention_note") or None,
    }


# ──────────────────────────────────────────────────────────── third parties

def _build_third_party(m: dict, r: importer.Resolver) -> dict:
    return {
        "name": require(m, "name", "Name"),
        "legal_name": m.get("legal_name") or None,
        "category": m.get("category") or None,
        "criticality": one_of(m, "criticality", CRITICALITY, "Criticality"),
        "status": one_of(m, "status", vocabularies.TP_STATUSES, "Status") or "ACTIVE",
        "business_owner_person_id": r.person(m.get("business_owner")),
        # A vendor's parent is another vendor — the bank's "4th party" chain.
        "parent_third_party_id": r.vendor(m.get("parent")),
    }


# ──────────────────────────────────────────────────────────── incidents

def _build_incident(m: dict, r: importer.Resolver) -> dict:
    return {
        "title": require(m, "title", "Title"),
        "reference": m.get("reference") or None,
        "description": m.get("description") or None,
        "severity": one_of(m, "severity", CRITICALITY, "Severity"),
        "category": m.get("category") or None,
        "detected_at": m.get("detected_at") or None,
        "owner_person_id": r.person(m.get("owner")),
        "status": one_of(m, "status", vocabularies.INCIDENT_STATUSES, "Status") or "OPEN",
        "root_cause": m.get("root_cause") or None,
    }


OWNER_HELP = "full name, or their email address if two people share a name"

SPECS: dict[str, dict] = {
    "risks": {
        "table": "risks", "module": "risks", "label_key": "title", "noun": "Risks",
        "columns": [
            _col("title", "Title", "what could go wrong", required=True),
            _col("reference", "Reference", "your own risk id, e.g. R-001"),
            _col("category", "Category", "matches the Risk category list in Masters"),
            _col("description", "Description"),
            _col("owner", "Owner", OWNER_HELP),
            _col("inherent_likelihood", "Inherent likelihood", "1-5"),
            _col("inherent_impact", "Inherent impact", "1-5"),
            _col("residual_likelihood", "Residual likelihood", "1-5"),
            _col("residual_impact", "Residual impact", "1-5"),
            _col("treatment", "Treatment", "MITIGATED, ACCEPTED, AVOIDED or TRANSFERRED"),
            _col("status", "Status", "OPEN or CLOSED"),
        ],
        "build": _build_risk,
    },
    "assets": {
        "table": "assets", "module": "assets", "label_key": "name", "noun": "Assets",
        "columns": [
            _col("name", "Name", required=True),
            _col("asset_type", "Type", "VIRTUAL or PHYSICAL"),
            _col("subtype", "Subtype", "matches the Asset subtype list in Masters"),
            _col("description", "Description"),
            _col("owner", "Owner", OWNER_HELP),
            _col("criticality", "Criticality", "LOW, MEDIUM, HIGH or CRITICAL"),
            _col("location", "Location"),
            _col("vendor", "Vendor", "name of a third party already on the register"),
        ],
        "build": _build_asset,
    },
    "data-items": {
        "table": "data_items", "module": "data", "label_key": "name", "noun": "Data items",
        "columns": [
            _col("name", "Name", required=True),
            _col("classification", "Classification",
                 "PUBLIC, INTERNAL, CONFIDENTIAL or SECRET"),
            _col("data_type", "Where it lives", "matches the Data type list in Masters"),
            _col("description", "Description"),
            _col("owner", "Owner", OWNER_HELP),
            _col("retention_note", "Retention"),
        ],
        "build": _build_data_item,
    },
    "third-parties": {
        "table": "third_parties", "module": "third_parties", "label_key": "name",
        "noun": "Third parties",
        "columns": [
            _col("name", "Name", required=True),
            _col("legal_name", "Legal name"),
            _col("category", "Category", "matches the Third-party category list in Masters"),
            _col("criticality", "Criticality", "LOW, MEDIUM, HIGH or CRITICAL"),
            _col("status", "Status", "ACTIVE, OFFBOARDING or TERMINATED"),
            _col("business_owner", "Business owner", OWNER_HELP),
            _col("parent", "Sub-processor of", "name of another third party already on the register"),
        ],
        "build": _build_third_party,
    },
    "incidents": {
        "table": "incidents", "module": "incidents", "label_key": "title", "noun": "Incidents",
        "columns": [
            _col("title", "Title", required=True),
            _col("reference", "Reference", "your own incident id, e.g. INC-001"),
            _col("category", "Category", "matches the Incident category list in Masters"),
            _col("severity", "Severity", "LOW, MEDIUM, HIGH or CRITICAL"),
            _col("detected_at", "Detected", "YYYY-MM-DD"),
            _col("owner", "Owner", OWNER_HELP),
            _col("status", "Status", "OPEN, INVESTIGATING, RESOLVED or CLOSED"),
            _col("root_cause", "Root cause"),
            _col("description", "Description"),
        ],
        "build": _build_incident,
    },
}
