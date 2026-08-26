"""Service-account auth for external integrations (P8-S0).

Machine callers (asset heartbeat pings, compliance-config-check agents) are not tenant
members and must never be routed through `require()`'s per-user RBAC resolution —
`IntegrationPrincipal` is deliberately a different shape from `auth.Principal`, which means
"a person with a role."

Token discipline mirrors `signing_tokens` / `api/routers/signing.py`: the raw value is a
high-entropy random string, sha256'd for storage, looked up directly by hash. Not argon2 —
argon2's deliberate slowness defends against guessing a LOW-entropy secret (a password); a
256-bit random token was never guessable, so hashing it with argon2 on every ingestion call
would only add latency for no security benefit.

Two-tier tokens (`kind`): a short-lived `install` token is generated in the UI for a human to
hand to a new agent during setup; the agent's first call exchanges it for a long-lived
`longlived` token it stores locally. Both rows live in `integration_tokens`, distinguished by
`kind` — not two tables.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import insert, select, update

from api.core.database import engine, t
from api.core.util import now_iso, now_plus_days

_bearer = HTTPBearer(auto_error=False)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


class IntegrationPrincipal(dict):
    """A machine caller — NOT a tenant member, no role, no RBAC.
    Keys: tenant_id, token_id, token_name."""

    @property
    def tenant_id(self) -> str:
        return self["tenant_id"]

    @property
    def token_id(self) -> str:
        return self["token_id"]

    @property
    def token_name(self) -> str:
        return self["token_name"]


def issue_install_token(conn, *, tenant_id: str, name: str, member_id: str | None) -> str:
    """24h install token. Caller owns the transaction (a JWT-authed route)."""
    raw = _generate_token()
    conn.execute(insert(t("integration_tokens")).values(
        id=str(uuid.uuid4()), tenant_id=tenant_id, name=name, token_hash=_hash(raw),
        kind="install", created_by_member_id=member_id, created_at=now_iso(),
        expires_at=now_plus_days(1)))  # 24h
    return raw


def exchange_install_token(raw_install_token: str) -> str:
    """An agent's first call: trade a live install token for a long-lived one, atomically
    revoking the install token so it cannot be redeemed twice. No JWT — the install token
    itself is the credential, same discipline as /sign."""
    it = t("integration_tokens")
    now = now_iso()
    with engine.begin() as conn:
        tok = conn.execute(select(it).where(
            it.c.token_hash == _hash(raw_install_token))).mappings().first()
        if tok is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This install token is not valid.")
        if tok["kind"] != "install":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not an install token.")
        if tok["revoked_at"]:
            raise HTTPException(status.HTTP_410_GONE, "This install token has been revoked.")
        if tok["expires_at"] <= now:
            raise HTTPException(status.HTTP_410_GONE, "This install token has expired.")
        conn.execute(update(it).where(it.c.id == tok["id"]).values(revoked_at=now))
        raw_longlived = _generate_token()
        conn.execute(insert(it).values(
            id=str(uuid.uuid4()), tenant_id=tok["tenant_id"], name=tok["name"],
            token_hash=_hash(raw_longlived), kind="longlived",
            created_by_member_id=tok["created_by_member_id"], created_at=now))
    return raw_longlived


def get_integration_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> IntegrationPrincipal:
    """Long-lived-token-authed dependency for ingestion endpoints (heartbeat, checks)."""
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    it = t("integration_tokens")
    now = now_iso()
    with engine.begin() as conn:
        tok = conn.execute(select(it).where(
            it.c.token_hash == _hash(creds.credentials))).mappings().first()
        if tok is None or tok["kind"] != "longlived":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        if tok["revoked_at"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This token has been revoked.")
        conn.execute(update(it).where(it.c.id == tok["id"]).values(last_used_at=now))
    return IntegrationPrincipal(tenant_id=tok["tenant_id"], token_id=tok["id"],
                                token_name=tok["name"])
