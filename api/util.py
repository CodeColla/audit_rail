"""Small shared helpers: timestamps and date arithmetic for validity / review."""

from __future__ import annotations

import calendar
import datetime as dt
import re
from typing import Annotated

from pydantic import BeforeValidator

# ── ISO date coercion ───────────────────────────────────────────────────────
# The `iso_ts` domain in db/schema_v2_postgres.sql rejects anything that isn't
# ISO-8601 — including the EMPTY STRING. A blank <input type="date"> posts "",
# not null, so an untouched optional date field would hit a CheckViolation and
# surface as a 500. Coerce "" -> None and reject junk with a 422 instead.
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.\+\-Z]*)?$")


def iso_or_none(v):
    """'' / whitespace -> None; validate anything else looks ISO-8601."""
    if v is None:
        return None
    if not isinstance(v, str):
        return v
    v = v.strip()
    if not v:
        return None
    if not _ISO_RE.match(v):
        raise ValueError(f"expected an ISO-8601 date (YYYY-MM-DD), got {v!r}")
    return v


#: Use for every optional date/timestamp field taken from a form or JSON body.
IsoDate = Annotated[str | None, BeforeValidator(iso_or_none)]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return dt.date.today().isoformat()


def add_months(iso_date: str, months: int) -> str:
    """Add whole months to a YYYY-MM-DD date, clamping the day to month length."""
    y, m, d = (int(x) for x in iso_date[:10].split("-"))
    total = (m - 1) + months
    y2, m2 = y + total // 12, total % 12 + 1
    d2 = min(d, calendar.monthrange(y2, m2)[1])
    return f"{y2:04d}-{m2:02d}-{d2:02d}"


def _days_until(iso_date: str | None, today: str) -> int | None:
    if not iso_date:
        return None
    a = dt.date.fromisoformat(iso_date[:10])
    b = dt.date.fromisoformat(today)
    return (a - b).days


def evidence_status(valid_until: str | None, today: str, soon_days: int = 60) -> str:
    """valid | expiring | expired | no_expiry"""
    days = _days_until(valid_until, today)
    if days is None:
        return "no_expiry"
    if days < 0:
        return "expired"
    return "expiring" if days <= soon_days else "valid"


def review_status(next_review_at: str | None, today: str, soon_days: int = 30) -> str:
    """overdue | due_soon | ok | none"""
    days = _days_until(next_review_at, today)
    if days is None:
        return "none"
    if days < 0:
        return "overdue"
    return "due_soon" if days <= soon_days else "ok"
