#!/usr/bin/env python3
"""Set a tenant's plan by hand (Phase 10, S2a) — Enterprise deals and manual fixes.

There is no admin UI for this yet; req/phase10/01-sprint-plan.md explicitly defers one.
Overrides are per-tenant and override the PLAN's own default (api/domain/plans.py), not
each other — pass only the ones you mean to change; omit the rest to leave them alone.

Usage:
  .venv/bin/python scripts/set_plan.py --tenant <id-or-slug> --plan enterprise
  .venv/bin/python scripts/set_plan.py --tenant <id-or-slug> --plan pro \
      --audits 50 --storage-gb 100
  .venv/bin/python scripts/set_plan.py --tenant <id-or-slug> --audits 20   # override only
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import insert, select, update  # noqa: E402

from _db import get_engine  # noqa: E402
from api.core.database import metadata, reflect_schema, t  # noqa: E402
from api.core.util import now_iso  # noqa: E402
from api.domain.plans import PLANS  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant", required=True, help="tenants.id or tenants.slug")
    p.add_argument("--plan", choices=sorted(PLANS), help="free | pro | enterprise")
    p.add_argument("--audits", type=int, help="override max active audits (0 disallowed; "
                                               "use a large number for 'effectively unlimited')")
    p.add_argument("--storage-gb", type=float, help="override storage limit, in GB")
    p.add_argument("--members", type=int, help="override max logins")
    p.add_argument("--clear-overrides", action="store_true",
                   help="remove all three overrides, falling back to the plan's own default")
    args = p.parse_args()

    engine = get_engine()
    reflect_schema()
    tenants, tp = t("tenants"), t("tenant_plans")

    with engine.begin() as conn:
        row = conn.execute(select(tenants.c.id, tenants.c.name).where(
            (tenants.c.id == args.tenant) | (tenants.c.slug == args.tenant))).mappings().first()
        if row is None:
            sys.exit(f"No tenant matches id or slug {args.tenant!r}")
        tid = row["id"]

        vals: dict = {"updated_at": now_iso()}
        if args.plan:
            vals["plan_code"] = args.plan
        if args.clear_overrides:
            vals["override_max_active_audits"] = None
            vals["override_storage_bytes"] = None
            vals["override_max_members"] = None
        if args.audits is not None:
            vals["override_max_active_audits"] = args.audits
        if args.storage_gb is not None:
            vals["override_storage_bytes"] = int(args.storage_gb * 1024 * 1024 * 1024)
        if args.members is not None:
            vals["override_max_members"] = args.members

        exists = conn.execute(select(tp.c.tenant_id).where(tp.c.tenant_id == tid)).first()
        if exists:
            conn.execute(update(tp).where(tp.c.tenant_id == tid).values(**vals))
        else:
            conn.execute(insert(tp).values(tenant_id=tid, **vals))

        final = conn.execute(select(tp).where(tp.c.tenant_id == tid)).mappings().first()

    print(f"{row['name']} ({tid}):")
    for k, v in dict(final).items():
        print(f"    {k} = {v}")


if __name__ == "__main__":
    main()
