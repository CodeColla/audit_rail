#!/usr/bin/env python3
"""Fold the legacy `policies` register into `documents` (Sprint 2 / M9a, DoD #5).

Each policy becomes a POLICY / AUTHORED document (`legacy_policy_id` records the
provenance); each policy_version becomes a PUBLISHED document_version carrying the
original file. Idempotent — a policy already migrated (a document with its
`legacy_policy_id`) is skipped.

Ownership note: `policies.owner_member_id` points at a tenant MEMBER (a login),
but `documents.owner_person_id` points at a PERSON (who may have no login). So we
resolve/create a person from the member's user, matching by email.

Run after init_db + seed_demo:  .venv/bin/python scripts/migrate_policies_to_documents.py
"""

import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from _db import get_engine  # noqa: E402
from api.util import now_iso  # noqa: E402


def uid() -> str:
    return str(uuid.uuid4())


def _person_for_member(conn, tenant_id: str, member_id: str | None) -> str:
    """Return a person id for a policy's owner member, creating one if needed."""
    if member_id is None:
        member_id = conn.execute(text(
            "SELECT id FROM tenant_members WHERE tenant_id = :t ORDER BY created_at LIMIT 1"),
            {"t": tenant_id}).scalar()
    u = conn.execute(text(
        "SELECT u.email, u.full_name FROM tenant_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.id = :m"), {"m": member_id}).mappings().first()
    email = (u["email"] if u else "compliance@kiam.example").lower()
    pid = conn.execute(text(
        "SELECT id FROM people WHERE tenant_id = :t AND email = :e"),
        {"t": tenant_id, "e": email}).scalar()
    if pid:
        return pid
    pid = uid()
    conn.execute(text(
        "INSERT INTO people (id,tenant_id,full_name,email,source) "
        "VALUES (:i,:t,:f,:e,'IMPORT')"),
        {"i": pid, "t": tenant_id, "f": (u["full_name"] if u else "Compliance"), "e": email})
    return pid


def _parse_version(label: str | None, fallback_major: int) -> tuple[int, int]:
    m = re.search(r"(\d+)(?:\.(\d+))?", label or "")
    if m:
        return int(m.group(1)), int(m.group(2) or 0)
    return fallback_major, 0


def main() -> None:
    engine = get_engine()
    migrated = skipped = versions = 0
    with engine.begin() as conn:
        policies = conn.execute(text("SELECT * FROM policies ORDER BY created_at")).mappings().all()
        for p in policies:
            exists = conn.execute(text(
                "SELECT 1 FROM documents WHERE legacy_policy_id = :p"), {"p": p["id"]}).first()
            if exists:
                skipped += 1
                continue
            owner = _person_for_member(conn, p["tenant_id"], p["owner_member_id"])
            doc_id, now = uid(), now_iso()
            conn.execute(text(
                "INSERT INTO documents (id,tenant_id,title,description,document_type,"
                "write_mode,owner_person_id,review_cadence_months,next_review_at,status,"
                "legacy_policy_id,created_at,updated_at) VALUES (:i,:t,:ti,:d,'POLICY',"
                "'AUTHORED',:o,:rc,:nr,:st,:lp,:c,:c)"),
                {"i": doc_id, "t": p["tenant_id"], "ti": p["title"], "d": p["description"],
                 "o": owner, "rc": p["review_cadence_months"], "nr": p["next_review_at"],
                 "st": "ACTIVE" if p["status"] == "active" else "ARCHIVED",
                 "lp": p["id"], "c": now})
            migrated += 1

            pvs = conn.execute(text(
                "SELECT * FROM policy_versions WHERE policy_id = :p "
                "ORDER BY effective_from NULLS FIRST, created_at"), {"p": p["id"]}).mappings().all()
            last_ver_id = None
            for i, pv in enumerate(pvs, 1):
                major, minor = _parse_version(pv["version_label"], i)
                vid = uid()
                conn.execute(text(
                    "INSERT INTO document_versions (id,tenant_id,document_id,major,minor,"
                    "content,status,published_at,file_id,changelog,created_at,updated_at) "
                    "VALUES (:i,:t,:d,:mj,:mn,'','PUBLISHED',:pa,:f,:cl,:c,:c)"),
                    {"i": vid, "t": p["tenant_id"], "d": doc_id, "mj": major, "mn": minor,
                     "pa": pv["effective_from"] or pv["created_at"], "f": pv["file_id"],
                     "cl": pv["notes"], "c": pv["created_at"]})
                last_ver_id = vid
                versions += 1
            # older versions become SUPERSEDED; newest stays PUBLISHED + becomes current
            if last_ver_id:
                conn.execute(text(
                    "UPDATE document_versions SET status='SUPERSEDED' "
                    "WHERE document_id = :d AND id <> :v"), {"d": doc_id, "v": last_ver_id})
                conn.execute(text(
                    "UPDATE documents SET current_published_version_id = :v WHERE id = :d"),
                    {"v": last_ver_id, "d": doc_id})

    print(f"policies migrated : {migrated}")
    print(f"already migrated  : {skipped}")
    print(f"versions folded   : {versions}")


if __name__ == "__main__":
    main()
