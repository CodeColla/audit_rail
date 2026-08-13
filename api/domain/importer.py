"""Shared bulk-row importer for the registers (P5-S5).

Promoted from `POST /people/import`, which already solved the part that matters: each row
runs in its own `conn.begin_nested()` so one bad row cannot abort the batch, and the response
is `{created, failed, errors:[{row, name, error}]}` rather than a single opaque failure.

**Name-to-id resolution is the substantive work here**, and it is why this is not a thin
wrapper. Every register references people and vendors by UUID, which nobody types into a
spreadsheet — they type "Priya Sharma". Resolving that is where a bulk importer either earns
its keep or silently corrupts a register, so the rules are strict and identical everywhere:

  1. Match a person by EMAIL first (exact, case-insensitive) — unambiguous by construction.
  2. Otherwise match by full name, case-insensitive, among ACTIVE people.
  3. Two matches -> the ROW FAILS, naming the ambiguity. Never guess, never take the first.
  4. Zero matches -> the row fails with the value quoted back.

Rule 3 is the one that matters. Picking arbitrarily between two people called "Priya Sharma"
would assign a risk to the wrong owner and look like a successful import.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from sqlalchemy import func, insert, select

from api.core.database import t
from api.core.util import now_iso


class RowError(Exception):
    """A problem with ONE row. Caught per row, reported, and the batch continues."""


class Ambiguous(RowError):
    pass


# ──────────────────────────────────────────────────────────── lookups

def _people_index(conn, tenant_id: str) -> tuple[dict, dict]:
    """(by_email, by_lowered_name) for this tenant. Built once per import, not per row —
    a 500-row file would otherwise be 1000 queries."""
    p = t("people")
    by_email: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for r in conn.execute(select(p.c.id, p.c.full_name, p.c.email, p.c.state)
                          .where(p.c.tenant_id == tenant_id)).mappings():
        if r["email"]:
            by_email[r["email"].strip().lower()] = r["id"]
        # Only ACTIVE people are name-matchable: a leaver with the same name as a current
        # employee must not silently win the match.
        if r["full_name"] and (r["state"] or "ACTIVE") == "ACTIVE":
            by_name.setdefault(r["full_name"].strip().lower(), []).append(r["id"])
    return by_email, by_name


def _vendor_index(conn, tenant_id: str) -> dict[str, list[str]]:
    tp = t("third_parties")
    out: dict[str, list[str]] = {}
    for r in conn.execute(select(tp.c.id, tp.c.name)
                          .where(tp.c.tenant_id == tenant_id)).mappings():
        if r["name"]:
            out.setdefault(r["name"].strip().lower(), []).append(r["id"])
    return out


class Resolver:
    """Turns the human-typed values in a spreadsheet into the ids the tables need."""

    def __init__(self, conn, tenant_id: str):
        self._by_email, self._by_name = _people_index(conn, tenant_id)
        self._vendors = _vendor_index(conn, tenant_id)

    def person(self, value: str | None) -> str | None:
        if not value:
            return None
        v = value.strip()
        if not v:
            return None
        hit = self._by_email.get(v.lower())
        if hit:
            return hit
        matches = self._by_name.get(v.lower(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise Ambiguous(f"more than one active person is called {v!r} — "
                            f"use their email address instead")
        raise RowError(f"no active person called {v!r} — add them under People first")

    def vendor(self, value: str | None) -> str | None:
        if not value:
            return None
        v = value.strip()
        if not v:
            return None
        matches = self._vendors.get(v.lower(), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise Ambiguous(f"more than one third party is called {v!r} — "
                            f"rename one, or import this row by hand")
        raise RowError(f"no third party called {v!r} — add the vendor first")


# ──────────────────────────────────────────────────────────── the import loop

def import_rows(
    conn,
    *,
    tenant_id: str,
    table: str,
    rows: list[dict],
    mapping: dict[str, str],
    build: Callable[[dict, Resolver], dict],
    label_key: str,
    friendly: Callable[[Exception], str],
    first_data_row: int = 2,
    extra: dict | None = None,
    timestamps: bool = True,
) -> dict[str, Any]:
    """Import `rows` into `table`, reporting per-row outcomes.

    `mapping` is {our_field: their_header} — supplied by the UI's column-mapping table, so we
    never guess which column is which. `build` turns one mapped row into the values to insert
    (and is where each register's own validation and id resolution happens); raising
    `RowError` from it fails just that row.

    `first_data_row` exists purely so the reported row numbers match what the user sees in
    Excel — off-by-one here makes every error message point at the wrong line.

    `extra` are fixed values every row gets that do NOT come from the spreadsheet — the
    framework a clause belongs to, for instance, which comes from the URL. `timestamps=False`
    is for tables that simply have no `created_at`/`updated_at` columns (`framework_clauses`
    is one); passing them anyway is an immediate error on every row.
    """
    resolver = Resolver(conn, tenant_id)
    created, errors = 0, []
    now = now_iso()

    for offset, raw in enumerate(rows):
        excel_row = first_data_row + offset
        mapped = {field: raw.get(header) for field, header in mapping.items()}
        label = (mapped.get(label_key) or "").strip() or None
        try:
            values = build(mapped, resolver)
        except RowError as e:
            errors.append({"row": excel_row, "name": label, "error": str(e)})
            continue
        except Exception as e:                                   # noqa: BLE001
            errors.append({"row": excel_row, "name": label, "error": friendly(e)})
            continue
        try:
            # One bad row must never abort the batch — the whole reason this is a SAVEPOINT
            # per row rather than one transaction for the file.
            with conn.begin_nested():
                conn.execute(insert(t(table)).values(
                    id=str(uuid.uuid4()), tenant_id=tenant_id,
                    **({"created_at": now, "updated_at": now} if timestamps else {}),
                    **(extra or {}), **values))
            created += 1
        except Exception as e:                                   # noqa: BLE001
            errors.append({"row": excel_row, "name": label, "error": friendly(e)})

    return {"created": created, "failed": len(errors), "errors": errors}


def require(mapped: dict, field: str, what: str) -> str:
    v = (mapped.get(field) or "").strip() if mapped.get(field) else ""
    if not v:
        raise RowError(f"{what} is required")
    return v


def one_of(mapped: dict, field: str, allowed: tuple[str, ...], what: str) -> str | None:
    """Case-insensitive match against a fixed vocabulary, returning the canonical spelling.
    Spreadsheets carry 'high', 'High' and 'HIGH' interchangeably; rejecting on case would be
    pedantry, but accepting a value the CHECK constraint will refuse is worse — it surfaces
    as an opaque database error instead of a row message."""
    raw = (mapped.get(field) or "").strip() if mapped.get(field) else ""
    if not raw:
        return None
    for a in allowed:
        if a.lower() == raw.lower():
            return a
    raise RowError(f"{what} must be one of: {', '.join(allowed)} (got {raw!r})")


def count_existing(conn, table: str, tenant_id: str) -> int:
    return conn.execute(select(func.count()).select_from(t(table))
                        .where(t(table).c.tenant_id == tenant_id)).scalar() or 0
