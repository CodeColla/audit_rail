#!/usr/bin/env python3
"""Mint an Enterprise license token (Phase 10 extension) — run on YOUR OWN machine, never
on a deployment you hand to a customer.

Prints the signed token to stdout. Hand it to the customer's Super Admin to paste into
their deployment's License screen (Settings → Billing, in enterprise mode), or — if it's
a dedicated instance on your own infra — apply it there yourself the same way.

Growing a deal is the same command again with bigger numbers: there is no need to revoke
the old token first, since whichever one is CURRENTLY APPLIED on their deployment is the
only one that matters.

Usage:
  .venv/bin/python scripts/issue_license.py --private-key ~/sr-licensing/private.pem \
      --customer "Acme Bank Pvt Ltd" --max-members 50 --storage-gb 200 --valid-days 365
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import jwt

ISSUER = "sr-associates-licensing"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--private-key", required=True, type=Path)
    p.add_argument("--customer", required=True, help='e.g. "Acme Bank Pvt Ltd"')
    p.add_argument("--max-members", required=True, type=int)
    p.add_argument("--storage-gb", required=True, type=int)
    p.add_argument("--valid-days", type=int, default=365)
    args = p.parse_args()

    if not args.private_key.exists():
        sys.exit(f"No private key at {args.private_key} — run "
                 f"scripts/generate_license_keypair.py once, first.")

    # Whole-second iat/exp, same reasoning as api/core/auth.py's login tokens: PyJWT's own
    # "now" is floored to the second, so a sub-second iat can land microscopically in the
    # future and raise ImmatureSignatureError the instant it's first checked.
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "iss": ISSUER,
        "customer": args.customer,
        "max_members": args.max_members,
        "storage_gb": args.storage_gb,
        "iat": now,
        "exp": now + dt.timedelta(days=args.valid_days),
    }
    token = jwt.encode(payload, args.private_key.read_text(), algorithm="RS256")
    print(token)

    exp_str = payload["exp"].strftime("%Y-%m-%d")
    print(f"\n# {args.customer}: {args.max_members} members, {args.storage_gb} GB, "
         f"valid until {exp_str} — sent to stderr-free stdout above, this line and up "
         f"are just for you.", file=sys.stderr)


if __name__ == "__main__":
    main()
