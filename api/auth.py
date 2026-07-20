"""Authentication & authorization (M1).

- Passwords: argon2id via argon2-cffi.
- Sessions: stateless JWT (PyJWT), HS256, carrying the user id, tenant id and role.
- `get_current_user` is the default-deny gate every protected router depends on.
- `require_roles(...)` layers coarse RBAC on top.

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

from api.config import settings
from api.database import engine, t

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
    now = dt.datetime.now(dt.timezone.utc)
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
    now = dt.datetime.now(dt.timezone.utc)
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
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
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


def require_roles(*roles: str):
    """Dependency factory: allow only the given tenant roles."""
    allowed = set(roles)

    def _dep(user: Principal = Depends(get_current_user)) -> Principal:
        if user.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role: {', '.join(sorted(allowed))}",
            )
        return user

    return _dep


def authenticate(email: str, password: str) -> Principal | None:
    """Verify credentials and resolve the user's (first) tenant membership."""
    users, members = t("users"), t("tenant_members")
    with engine.connect() as conn:
        u = conn.execute(
            select(users).where(users.c.email == email.lower().strip())
        ).mappings().first()
        if u is None or u["status"] == "disabled":
            return None
        if not verify_password(password, u["password_hash"]):
            return None
        m = conn.execute(
            select(members.c.tenant_id, members.c.role)
            .where(members.c.user_id == u["id"])
            .limit(1)
        ).mappings().first()
    if m is None:
        return None
    return Principal(
        user_id=u["id"], tenant_id=m["tenant_id"], role=m["role"],
        kind="member", email=u["email"], full_name=u["full_name"],
    )
