#!/usr/bin/env python3
"""Re-sync every tenant's SYSTEM roles against api/core/permissions.py's current MODULES.

`permissions.seed_roles()` already re-syncs idempotently (it deletes and re-inserts a
system role's `role_permissions` every time it runs — see its own docstring) — it just
only ever runs when an org is CREATED. Adding a module (Phase 10: "billing") means every
existing tenant's Admin/Editor/Viewer rows are missing it until something re-runs that
seeding for them. This is that something: run it once after any `MODULES` change that
should reach tenants that already exist.

Usage:
  .venv/bin/python scripts/resync_roles.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from _db import get_engine  # noqa: E402
from api.core import permissions  # noqa: E402
from api.core.database import reflect_schema, t  # noqa: E402


def main() -> None:
    engine = get_engine()
    reflect_schema()
    tenants = t("tenants")
    with engine.begin() as conn:
        ids = conn.execute(select(tenants.c.id, tenants.c.name)).all()
        for tid, name in ids:
            permissions.seed_roles(conn, tid)
            print(f"resynced {name} ({tid})")
    print(f"{len(ids)} tenant(s) resynced.")


if __name__ == "__main__":
    main()
