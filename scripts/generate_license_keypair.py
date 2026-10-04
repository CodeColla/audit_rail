#!/usr/bin/env python3
"""One-time setup for Enterprise licensing (Phase 10 extension).

Generates the RSA keypair that `scripts/issue_license.py` signs with and every
deployment's `api/domain/licensing.py` verifies against. Run this ONCE, ever — not per
customer, not per deployment. The same public key ships in every Docker image (Public and
Enterprise alike); only the private key, which never leaves wherever you choose to keep
it, lets you mint a license at all.

Usage:
  .venv/bin/python scripts/generate_license_keypair.py --private-key ~/sr-licensing/private.pem

The public half is written straight to api/license_public_key.pem (commit it — it isn't
secret, every deployment needs it). The private half is written ONLY to the path you give
with --private-key, which should be well outside this repo. If you ever lose it, every
license you've issued still verifies fine (that only needs the public key, already
committed) — you just can't mint a NEW one without generating a fresh pair and accepting
that every already-issued license still carries the OLD public key's signature, so the
new pair's public half would need shipping to every deployment again. Treat the private
key like you would any other signing key: one copy, backed up somewhere that isn't this
repo.
"""

import argparse
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO = Path(__file__).resolve().parent.parent
PUBLIC_KEY_PATH = REPO / "api" / "license_public_key.pem"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--private-key", required=True, type=Path,
                  help="where to write the PRIVATE key — keep this OUTSIDE the repo")
    p.add_argument("--force", action="store_true",
                  help="overwrite an existing keypair (every already-issued license "
                       "will stop verifying against the NEW public key)")
    args = p.parse_args()

    if PUBLIC_KEY_PATH.exists() and not args.force:
        sys.exit(f"{PUBLIC_KEY_PATH} already exists — pass --force if you really mean to "
                 f"replace it (every license issued under the old key stops verifying).")
    if args.private_key.exists() and not args.force:
        sys.exit(f"{args.private_key} already exists — pass --force to overwrite it.")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    args.private_key.chmod(0o600)

    PUBLIC_KEY_PATH.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))

    print(f"Private key written to {args.private_key} (chmod 600) — back this up OUTSIDE "
         f"the repo and keep it somewhere safe. It is never committed.")
    print(f"Public key written to {PUBLIC_KEY_PATH} — commit this. "
         f"It ships in every deployment and verifies, but cannot mint, a license.")


if __name__ == "__main__":
    main()
