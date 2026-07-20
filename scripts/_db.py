"""Shared DB helpers for the seed/maintenance scripts (PostgreSQL).

The scripts used to talk raw `sqlite3`. Now they go through SQLAlchemy so they
use the same `DATABASE_URL` (and driver) as the app — see api/config.py.

`split_sql` exists because psycopg's extended query protocol will not run a
multi-statement string; we split db/schema.sql into individual statements and
execute them one at a time. It strips `--` comments and respects single-quoted
strings so a `;` inside a literal can't split a statement.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sqlalchemy import create_engine, text  # noqa: E402

from api.config import settings  # noqa: E402

SCHEMA_PATH = REPO / "db" / "schema.sql"


def get_engine():
    """Engine bound to DATABASE_URL (Postgres by default)."""
    return create_engine(settings.database_url, future=True)


def split_sql(sql: str) -> list[str]:
    """Split a SQL script into executable statements.

    Handles: `--` line comments, single-quoted literals (incl. '' escapes),
    and dollar-quoted blocks ($$ ... $$). Ignores semicolons inside those.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_str = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if dollar_tag:                                   # inside $tag$ ... $tag$
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch); i += 1; continue

        if in_str:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":                           # '' escape
                    buf.append(nxt); i += 2; continue
                in_str = False
            i += 1
            continue

        if ch == "-" and nxt == "-":                     # line comment
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "'":
            in_str = True; buf.append(ch); i += 1; continue
        if ch == "$":                                    # maybe dollar quote
            j = sql.find("$", i + 1)
            if j != -1 and sql[i + 1:j].isidentifier() or (j == i + 1):
                dollar_tag = sql[i:j + 1]
                buf.append(dollar_tag); i = j + 1; continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []; i += 1; continue

        buf.append(ch); i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def reset_schema(conn) -> None:
    """Drop and recreate the `public` schema — a clean slate."""
    conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
    conn.execute(text("CREATE SCHEMA public"))


def apply_schema(conn, path: Path | None = None) -> int:
    """Execute a schema file statement by statement. Returns statement count.

    Defaults to db/schema.sql (v1, live). Pass `path` — or set SCHEMA_FILE — to
    apply db/schema_v2_postgres.sql instead; nothing else references v2 yet, so
    this switch is how the v2 cutover gets exercised.
    """
    target = path or Path(os.environ.get("SCHEMA_FILE", SCHEMA_PATH))
    stmts = split_sql(Path(target).read_text())
    for s in stmts:
        conn.execute(text(s))
    return len(stmts)
