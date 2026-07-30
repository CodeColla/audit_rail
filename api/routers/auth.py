"""Auth router — the only endpoints reachable without a token (plus self-service account
management, which needs one).

P4-S1 adds real identity: an organisation is a `tenants` row keyed by a unique GSTIN, the
first person to sign up becomes its Super Admin, and passwords obey a policy (see
api/passwords.py). A login can belong to several organisations and switch between them.
"""

import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from api import activity, gstin, passwords, permissions, vocabularies
from api.auth import (Principal, authenticate, create_access_token, get_caller,
                      get_current_user, memberships)
from api.database import engine, t
from api.util import StrictModel, now_iso, now_plus_days

router = APIRouter(prefix="/auth", tags=["auth"])

INVITE_TTL_DAYS = 14


def _slug(name: str, conn) -> str:
    """A URL-safe, unique slug for the organisation."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "org").lower()).strip("-")[:40] or "org"
    slug, n = base, 1
    while conn.execute(select(t("tenants").c.id).where(t("tenants").c.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _create_org(conn, *, name: str, gst: str, super_admin_user_id: str) -> str:
    try:
        gst_clean = gstin.validate(gst)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if conn.execute(select(t("tenants").c.name).where(
            t("tenants").c.gst_number == gst_clean)).first():
        raise HTTPException(409, "an organisation with that GST number already exists")
    tid = str(uuid.uuid4())
    conn.execute(insert(t("tenants")).values(
        id=tid, name=name.strip(), slug=_slug(name, conn), gst_number=gst_clean,
        super_admin_user_id=super_admin_user_id, status="active", created_at=now_iso()))
    permissions.seed_roles(conn, tid)          # every org gets Admin / Editor / Viewer
    vocabularies.seed(conn, tid)               # …and a starting set of dropdowns
    return tid


def _add_member(conn, tenant_id: str, user_id: str, role: str,
                role_name: str | None = None) -> None:
    """Add a membership. `role` is the legacy text column; `role_name` picks the RBAC role."""
    role_id = None
    if role_name:
        role_id = conn.execute(select(t("roles").c.id).where(
            t("roles").c.tenant_id == tenant_id, t("roles").c.name == role_name)).scalar()
    conn.execute(insert(t("tenant_members")).values(
        id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
        role=role, role_id=role_id, created_at=now_iso()))


# --------------------------------------------------------------------- login

class LoginIn(StrictModel):
    email: str
    password: str
    tenant_id: str | None = None          # which organisation, when they belong to several


class TokenOut(StrictModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str
    full_name: str
    organisations: list[dict] = []
    permissions: list[str] = []
    is_super_admin: bool = False
    password_expires_in_days: int | None = None
    must_change_password: bool = False


def _issue(principal: Principal) -> TokenOut:
    token = create_access_token(user_id=principal.user_id, tenant_id=principal.tenant_id,
                                role=principal.role)
    with engine.begin() as conn:
        orgs = memberships(conn, principal.user_id)
        left = passwords.days_until_expiry(conn, principal.user_id)
        perms = permissions.permissions_for(conn, principal.user_id, principal.tenant_id)
        conn.execute(update(t("users")).where(t("users").c.id == principal.user_id)
                     .values(last_login_at=now_iso()))
    return TokenOut(
        access_token=token, role=principal.role, tenant_id=principal.tenant_id,
        full_name=principal["full_name"],
        organisations=[{"tenant_id": o["tenant_id"], "name": o["tenant_name"],
                        "role": o["role"]} for o in orgs],
        permissions=sorted(f"{m}.{a}" for m, a in perms),
        is_super_admin=any(o["super_admin_user_id"] == principal.user_id for o in orgs),
        password_expires_in_days=left,
        must_change_password=left is not None and left < 0)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    principal = authenticate(body.email, body.password, body.tenant_id)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    activity.log(tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
                 action="auth.login", entity_type="user", entity_id=principal.user_id)
    return _issue(principal)


class SwitchIn(StrictModel):
    tenant_id: str


@router.post("/switch-org", response_model=TokenOut)
def switch_org(body: SwitchIn, user: Principal = Depends(get_current_user)):
    """Re-issue the token against another of the caller's organisations.

    Every query in the app scopes by the token's tenant, so swapping the token is all that
    switching requires — no other code changes.
    """
    with engine.connect() as conn:
        mine = memberships(conn, user.user_id)
    m = next((x for x in mine if x["tenant_id"] == body.tenant_id), None)
    if m is None:
        raise HTTPException(403, "you are not a member of that organisation")
    return _issue(Principal(user_id=user.user_id, tenant_id=m["tenant_id"], role=m["role"],
                            kind="member", email=user["email"], full_name=user["full_name"]))


# --------------------------------------------------------------------- signup

class SignupIn(StrictModel):
    full_name: str
    email: str
    password: str
    organisation_name: str
    gst_number: str


@router.post("/signup", status_code=201, response_model=TokenOut)
def signup(body: SignupIn):
    """Create an account AND its organisation. The signer becomes its Super Admin."""
    email = body.email.lower().strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "that does not look like an email address")
    try:
        passwords.validate(body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))

    users = t("users")
    with engine.begin() as conn:
        if conn.execute(select(users.c.id).where(users.c.email == email)).first():
            raise HTTPException(409, "an account with that email already exists — sign in instead")
        uid = str(uuid.uuid4())
        conn.execute(insert(users).values(
            id=uid, email=email, full_name=body.full_name.strip(), auth_provider="local",
            is_platform_admin=0, status="active", created_at=now_iso()))
        tid = _create_org(conn, name=body.organisation_name, gst=body.gst_number,
                          super_admin_user_id=uid)
        _add_member(conn, tid, uid, "admin", "Admin")
        passwords.set_password(conn, uid, body.password)

    activity.log(tenant_id=tid, actor_user_id=uid, action="org.created",
                 entity_type="tenant", entity_id=tid,
                 detail={"name": body.organisation_name})
    principal = authenticate(email, body.password, tid)
    return _issue(principal)


class OrgIn(StrictModel):
    name: str
    gst_number: str


@router.post("/orgs", status_code=201)
def create_org(body: OrgIn, user: Principal = Depends(get_current_user)):
    """A Super Admin can run more than one organisation."""
    with engine.begin() as conn:
        owns = conn.execute(select(t("tenants").c.id).where(
            t("tenants").c.super_admin_user_id == user.user_id)).first()
        if not owns:
            raise HTTPException(403, "only a Super Admin can create an organisation")
        tid = _create_org(conn, name=body.name, gst=body.gst_number,
                          super_admin_user_id=user.user_id)
        _add_member(conn, tid, user.user_id, "admin", "Admin")
    activity.log(tenant_id=tid, actor_user_id=user.user_id, action="org.created",
                 entity_type="tenant", entity_id=tid, detail={"name": body.name})
    return {"tenant_id": tid, "name": body.name}


# ------------------------------------------------------------ password lifecycle

class ChangePasswordIn(StrictModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordIn, user: Principal = Depends(get_current_user)):
    users = t("users")
    with engine.begin() as conn:
        row = conn.execute(select(users.c.password_hash).where(
            users.c.id == user.user_id)).mappings().first()
        from api.auth import verify_password
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(400, "your current password is not correct")
        try:
            passwords.set_password(conn, user.user_id, body.new_password)
        except ValueError as e:
            raise HTTPException(400, str(e))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="auth.password_changed", entity_type="user", entity_id=user.user_id)
    return {"ok": True}


class AcceptInviteIn(StrictModel):
    token: str
    password: str


@router.post("/accept-invite", response_model=TokenOut)
def accept_invite(body: AcceptInviteIn, request: Request):
    """Set your own password from an invite link — admins never type one for you."""
    import hashlib
    th, now = hashlib.sha256(body.token.encode()).hexdigest(), now_iso()
    inv, users = t("user_invites"), t("users")
    with engine.begin() as conn:
        # single-use, race-free: exactly one caller flips consumed_at
        row = conn.execute(inv.update()
                           .where(inv.c.token_hash == th, inv.c.consumed_at.is_(None),
                                  inv.c.revoked_at.is_(None), inv.c.expires_at > now)
                           .values(consumed_at=now)
                           .returning(inv)).mappings().first()
        if row is None:
            raise HTTPException(410, "This invitation link is no longer valid.")
        try:
            passwords.set_password(conn, row["user_id"], body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        u = conn.execute(select(users.c.email).where(
            users.c.id == row["user_id"])).mappings().first()
    principal = authenticate(u["email"], body.password, row["tenant_id"])
    if principal is None:
        raise HTTPException(400, "invitation accepted, but sign-in failed — please log in")
    return _issue(principal)


def issue_invite(conn, *, tenant_id: str, user_id: str, invited_by: str | None) -> str:
    """Mint an invite and return the RAW token — stored hashed, shown once.

    Used by the People module (P4-S2) when a person is given a login.
    """
    raw = secrets.token_urlsafe(32)
    import hashlib
    conn.execute(insert(t("user_invites")).values(
        id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(), invited_by_user_id=invited_by,
        issued_at=now_iso(), expires_at=now_plus_days(INVITE_TTL_DAYS)))
    return raw


# --------------------------------------------------------------------- me

@router.get("/me")
def me(user: Principal = Depends(get_caller)):
    out = {
        "user_id": user.user_id, "email": user["email"], "full_name": user["full_name"],
        "tenant_id": user.tenant_id, "role": user.role, "kind": user["kind"],
        "assessment_id": user.get("assessment_id"),
    }
    if user["kind"] == "member":
        with engine.connect() as conn:
            orgs = memberships(conn, user.user_id)
            left = passwords.days_until_expiry(conn, user.user_id)
        out["organisations"] = [{"tenant_id": o["tenant_id"], "name": o["tenant_name"],
                                 "role": o["role"]} for o in orgs]
        out["permissions"] = sorted(f"{m}.{a}" for m, a in permissions.load_permissions(user))
        out["is_super_admin"] = any(o["super_admin_user_id"] == user.user_id for o in orgs)
        out["password_expires_in_days"] = left
        out["must_change_password"] = left is not None and left < 0
    return out
