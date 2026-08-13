"""Authentication & authorization (M1).

- Passwords: argon2id via argon2-cffi.
- Sessions: stateless JWT (PyJWT), HS256, carrying the user id, tenant id and role.
- `get_current_user` is the default-deny gate every protected router depends on.
- Authorisation is api/permissions.require(module, action) — RBAC, resolved per
  request from the DB (see that module). `require_roles` was its coarse predecessor
  and is gone.

Tenancy: a token is scoped to exactly one tenant + role (the membership chosen at
login). Auditor guests are modeled separately (assessment_guests) and are out of
scope for M1 member auth — the principal shape leaves room for them (`kind`).
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from api.core.config import settings
from api.core.database import engine, t

_ph = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


# ----------------------------------------------------------------- passwords

def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False


# --------------------------------------------------------------------- JWT

def create_access_token(*, user_id: str, tenant_id: str, role: str) -> str:
    # Whole-second iat/exp (NumericDate). PyJWT validates iat against a now() it FLOORS to
    # the second; a sub-second iat can land microscopically in the "future" and raise
    # ImmatureSignatureError on the very next request (~1 login in 10^5). Truncating fixes it.
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "kind": "member",
        "iat": now,
        "exp": now + dt.timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_guest_token(*, user_id: str, assessment_id: str,
                       expires_at: str | None = None) -> str:
    """Auditor guest token: scoped to one assessment, expiring."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)   # whole-second iat/exp (see above)
    if expires_at:
        exp = dt.datetime.fromisoformat(expires_at[:10]).replace(
            hour=23, minute=59, tzinfo=dt.timezone.utc)
    else:
        exp = now + dt.timedelta(days=30)
    payload = {"sub": user_id, "aid": assessment_id, "role": "auditor",
               "kind": "guest", "iat": now, "exp": exp}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict:
    try:
        # leeway=10s absorbs iat/exp second-boundary rounding (PyJWT floors its own "now",
        # so a just-issued token can look a hair early) and minor multi-host clock skew.
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
                          leeway=10)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


# ------------------------------------------------------------- dependencies

class Principal(dict):
    """Current caller. Keys: user_id, tenant_id, role, kind, email, full_name,
    assessment_id (guests only)."""

    @property
    def user_id(self) -> str:
        return self["user_id"]

    @property
    def tenant_id(self) -> str:
        return self["tenant_id"]

    @property
    def role(self) -> str:
        return self["role"]

    @property
    def kind(self) -> str:
        return self["kind"]


def get_caller(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Any authenticated caller — tenant member OR auditor guest."""
    if creds is None or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    claims = _decode(creds.credentials)
    users = t("users")
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.email, users.c.full_name, users.c.status)
            .where(users.c.id == claims.get("sub"))
        ).mappings().first()
    if row is None or row["status"] == "disabled":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer active")
    kind = claims.get("kind", "member")
    return Principal(
        user_id=row["id"],
        tenant_id=claims.get("tid"),
        role=claims.get("role") or ("auditor" if kind == "guest" else None),
        kind=kind,
        email=row["email"],
        full_name=row["full_name"],
        assessment_id=claims.get("aid"),
    )


def get_current_user(caller: Principal = Depends(get_caller)) -> Principal:
    """Tenant-member callers only (guests are rejected)."""
    if caller.kind != "member":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Member access required")
    return caller




def memberships(conn, user_id: str) -> list[dict]:
    """Every organisation this login belongs to, oldest first (a stable default)."""
    members, tenants = t("tenant_members"), t("tenants")
    return [dict(r) for r in conn.execute(
        select(members.c.tenant_id, members.c.role, tenants.c.name.label("tenant_name"),
               tenants.c.super_admin_user_id)
        .join(tenants, members.c.tenant_id == tenants.c.id)
        .where(members.c.user_id == user_id)
        .order_by(members.c.created_at, members.c.id)).mappings()]


def authenticate(email: str, password: str, tenant_id: str | None = None) -> Principal | None:
    """Verify credentials and resolve a tenant membership.

    `tenant_id` picks the organisation when the login belongs to several (P4 org switching).
    Without it we take the FIRST membership by a deterministic order — this used to be
    `.limit(1)` with no ORDER BY, so a multi-org user landed in an arbitrary organisation
    that could change between logins.
    """
    users = t("users")
    with engine.connect() as conn:
        u = conn.execute(
            select(users).where(users.c.email == email.lower().strip())
        ).mappings().first()
        if u is None or u["status"] == "disabled":
            return None
        if not verify_password(password, u["password_hash"]):
            return None
        mine = memberships(conn, u["id"])
    if not mine:
        return None
    m = next((x for x in mine if x["tenant_id"] == tenant_id), None) if tenant_id else mine[0]
    if m is None:
        return None                                   # asked for an org they don't belong to
    return Principal(
        user_id=u["id"], tenant_id=m["tenant_id"], role=m["role"],
        kind="member", email=u["email"], full_name=u["full_name"],
    )
