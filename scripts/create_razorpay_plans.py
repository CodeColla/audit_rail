#!/usr/bin/env python3
"""Create the 6 Razorpay Plans this product sells (Phase 10, S3a) — run ONCE per
environment (once in test mode, once again in live mode when S4 goes live). Razorpay has
no "get or create by name," so running this twice in the SAME mode creates duplicate
plans — if that happens, delete the extras from the Razorpay dashboard by hand (there is
no delete-plan API).

Prices and limits come straight from api/domain/plans.py — the ONE place they're defined
— so this can never drift from what the app actually sells; if you've just changed a
price there, re-run this before putting the new RAZORPAY_PLAN_* ids into use.

Prints the six RAZORPAY_PLAN_* lines to paste into .env (local) or _docker/env/api.env
(deployed) — see api/core/config.py for exactly which setting each one is.

Usage:
  RAZORPAY_KEY_ID=rzp_test_... RAZORPAY_KEY_SECRET=... \
    .venv/bin/python scripts/create_razorpay_plans.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Settings reads RAZORPAY_KEY_ID/SECRET from the environment or .env — set before import,
# same reasoning as tests/conftest.py's own note on api.core.config's cached singleton.
if not os.environ.get("RAZORPAY_KEY_ID") or not os.environ.get("RAZORPAY_KEY_SECRET"):
    print("Reading RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from .env, if set there.",
         file=sys.stderr)

from api.domain import razorpay_client  # noqa: E402
from api.domain.plans import ADDONS, PLANS  # noqa: E402

ITEMS = [
    ("pro", "Auditrail Pro", PLANS["pro"]["price_paise"]),
    ("addon_audits", ADDONS["audits"]["name"], ADDONS["audits"]["price_paise"]),
    ("addon_storage", ADDONS["storage"]["name"], ADDONS["storage"]["price_paise"]),
]
SETTING_NAME = {
    ("pro", "monthly"): "RAZORPAY_PLAN_PRO_MONTHLY",
    ("pro", "yearly"): "RAZORPAY_PLAN_PRO_YEARLY",
    ("addon_audits", "monthly"): "RAZORPAY_PLAN_ADDON_AUDITS_MONTHLY",
    ("addon_audits", "yearly"): "RAZORPAY_PLAN_ADDON_AUDITS_YEARLY",
    ("addon_storage", "monthly"): "RAZORPAY_PLAN_ADDON_STORAGE_MONTHLY",
    ("addon_storage", "yearly"): "RAZORPAY_PLAN_ADDON_STORAGE_YEARLY",
}


def main() -> None:
    print("Creating 6 Razorpay plans — this is a ONE-TIME step per environment (test or "
         "live). Ctrl-C now if you've already run this in this mode.\n", file=sys.stderr)

    lines = []
    for item, name, prices in ITEMS:
        for cycle, amount in prices.items():
            plan = razorpay_client.create_plan(
                name=f"{name} ({cycle})", amount_paise=amount, period=cycle, interval=1,
                description="Auditrail — req/phase10/00-issue-19.md")
            setting = SETTING_NAME[(item, cycle)]
            print(f"  {item}/{cycle}: {plan['id']}", file=sys.stderr)
            lines.append(f"{setting}={plan['id']}")

    print("\n# paste into .env (local) or _docker/env/api.env (deployed):")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
