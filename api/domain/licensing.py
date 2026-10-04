"""Enterprise license tokens (Phase 10 extension — req/phase10/00-issue-19.md,
docs/phase10/03-enterprise-licensing.md).

Only relevant when `settings.deployment_mode == "enterprise"`. A license is a signed JWT
the vendor mints OFFLINE with an RSA private key that never leaves their own machine (see
scripts/issue_license.py) — the app only ever holds the PUBLIC half
(api/license_public_key.pem, checked in; it isn't secret). That asymmetry is the whole
point: on a deployment the CUSTOMER runs, full database and filesystem access still
can't forge a bigger license, because minting one needs the private key and verifying one
doesn't. `api/core/auth.py`'s login JWTs are HS256 (one shared secret both signs and
verifies) — fine there, because the server that signs is the same server that verifies.
Here those are two different parties, so this is RS256 instead; everything else about the
shape (whole-second timestamps, decode wrapped against bad input) mirrors that module.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import jwt

from api.core.config import settings
from api.core.logging import logger

#: Checked on every decode — a token signed by the right KEY but meant for something else
#: entirely must still be refused.
ISSUER = "sr-associates-licensing"

GB = 1024 * 1024 * 1024


@dataclasses.dataclass
class License:
    customer: str
    max_members: int
    storage_bytes: int
    issued_at: str   # iso_ts-shaped, derived from the token's iat
    expires_at: str  # iso_ts-shaped, derived from the token's exp
    expired: bool    # exp has already passed — decode() still returns the claims either way


def _public_key() -> str | None:
    p = Path(settings.license_public_key_path)
    if not p.exists():
        return None
    return p.read_text()


def decode(token: str) -> License | None:
    """Verify signature + issuer. Returns `None` only when the token fails verification
    outright (bad signature, wrong issuer, malformed claims) — NEVER on expiry alone. An
    expired-but-genuinely-signed token still decodes, so `entitlements.py` can tell a
    customer *why* their deployment changed ("your license expired on 2027-01-01") rather
    than just refusing to read it.
    """
    key = _public_key()
    if key is None:
        logger.warning(
            f"licensing.decode called with no public key at {settings.license_public_key_path}")
        return None
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER,
                           options={"verify_exp": False})
    except jwt.InvalidTokenError as e:
        logger.warning(f"license token failed verification: {e}")
        return None
    try:
        exp = dt.datetime.fromtimestamp(claims["exp"], tz=dt.timezone.utc)
        iat = dt.datetime.fromtimestamp(claims["iat"], tz=dt.timezone.utc)
        return License(
            customer=str(claims["customer"]),
            max_members=int(claims["max_members"]),
            storage_bytes=int(claims["storage_gb"]) * GB,
            issued_at=iat.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expired=dt.datetime.now(dt.timezone.utc) > exp,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"license token has missing or malformed claims: {e}")
        return None
