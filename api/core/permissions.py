"""RBAC — module × action permissions (P4-S2).

The vocabulary lives here rather than in a CHECK constraint, so adding a module is a code
change, not a migration. A role is a bundle of (module, action) pairs; a membership points
at one role.

Resolution order for a caller, cheapest first:

  1. **Super Admin** of the organisation (`tenants.super_admin_user_id`) → everything.
     They own the org; locking them out of their own tenant is never useful.
  2. **`tenant_members.role_id`** → that role's rows in `role_permissions`.
  3. **Legacy fallback** — a membership with no `role_id` (pre-P4 rows, and fixtures that
     insert memberships directly) resolves through LEGACY_ROLE_MAP so nothing that worked
     before P4-S2 breaks. New members always get an explicit role.

Permissions are read from the DB **per request**, never trusted from the JWT: `get_caller`
takes `role`/`tid` straight from the token and never re-checks membership, so a revoked or
demoted member would otherwise keep their access until the 12h token expired.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select

from api.core.auth import Principal, get_current_user
from api.core.database import engine, t

# ── vocabulary ──────────────────────────────────────────────────────────────────────────
VIEW, ADD, EDIT, DELETE = "view", "add", "edit", "delete"
APPROVE, PUBLISH = "approve", "publish"
CRUD = (VIEW, ADD, EDIT, DELETE)

#: module -> the actions that mean something for it. This drives the role checkbox matrix.
MODULES: dict[str, tuple[str, ...]] = {
    "dashboard":     (VIEW,),
    "people":        CRUD,
    "users":         CRUD,                      # logins, memberships, invites
    "roles":         CRUD,                      # the RBAC screen itself
    "org":           (VIEW, EDIT, ADD),         # organisation settings; ADD = create another
    "audits":        CRUD,
    "controls":      CRUD,
    "evidence":      CRUD,
    "documents":     CRUD + (APPROVE, PUBLISH),
    "risks":         CRUD,
    "assets":        CRUD,
    "data":          CRUD,
    "third_parties": CRUD,
    "incidents":     CRUD,
    "obligations":   CRUD,                      # hidden in the UI, still permissioned
    "tasks":         CRUD,
    "reports":       (VIEW,),
}

#: Human labels for the role editor.
MODULE_LABELS = {
    "dashboard": "Dashboard", "people": "People", "users": "Users & logins",
    "roles": "Roles & permissions", "org": "Organisation", "audits": "Audits",
    "controls": "Controls", "evidence": "Evidence", "documents": "Documents",
    "risks": "Risks", "assets": "Assets", "data": "Data inventory",
    "third_parties": "Third parties", "incidents": "Incidents",
    "obligations": "Obligations", "tasks": "Tasks", "reports": "Reports",
}


#: Administering the workspace itself. Only Admin (and the Super Admin) gets these.
ADMIN_MODULES = ("users", "roles", "org")


def all_permissions() -> set[tuple[str, str]]:
    return {(m, a) for m, actions in MODULES.items() for a in actions}


def _perms(modules: dict[str, tuple[str, ...]]) -> set[tuple[str, str]]:
    return {(m, a) for m, acts in modules.items() for a in acts}


# ── system roles, seeded into every organisation ────────────────────────────────────────
SYSTEM_ROLES: dict[str, dict] = {
    "Admin": {
        "description": "Full access — the compliance department head.",
        "permissions": all_permissions,
    },
    "Editor": {
        "description": "Can create and edit compliance records, but not delete them or "
                       "manage users.",
        "permissions": lambda: _perms({
            m: tuple(a for a in acts if a != DELETE)
            for m, acts in MODULES.items() if m not in ADMIN_MODULES
        }),
    },
    "Viewer": {
        "description": "Read-only across the workspace.",
        "permissions": lambda: {(m, VIEW) for m, acts in MODULES.items()
                                if VIEW in acts and m not in ADMIN_MODULES},
    },
}

#: pre-P4 membership roles → the system role they behave as.
LEGACY_ROLE_MAP = {"admin": "Admin", "manager": "Admin", "member": "Editor"}


def seed_roles(conn, tenant_id: str) -> dict[str, str]:
    """Create (idempotently) the system roles for an organisation. Returns name -> role_id."""
    from sqlalchemy import insert
    import uuid

    roles, rp = t("roles"), t("role_permissions")
    out: dict[str, str] = {}
    for name, spec in SYSTEM_ROLES.items():
        rid = conn.execute(select(roles.c.id).where(
            roles.c.tenant_id == tenant_id, roles.c.name == name)).scalar()
        if rid is None:
            rid = str(uuid.uuid4())
            conn.execute(insert(roles).values(
                id=rid, tenant_id=tenant_id, name=name,
                description=spec["description"], is_system=1))
        # System roles are defined in code, so their permissions are re-synced every time —
        # otherwise a change to MODULES would silently never reach existing organisations.
        from sqlalchemy import delete
        conn.execute(delete(rp).where(rp.c.role_id == rid))
        conn.execute(insert(rp), [{"role_id": rid, "module": m, "action": a}
                                  for m, a in sorted(spec["permissions"]())])
        out[name] = rid
    return out


# ── resolution ──────────────────────────────────────────────────────────────────────────

def permissions_for(conn, user_id: str, tenant_id: str) -> set[tuple[str, str]]:
    """Everything this caller may do in this organisation."""
    if conn.execute(select(t("tenants").c.id).where(
            t("tenants").c.id == tenant_id,
            t("tenants").c.super_admin_user_id == user_id)).first():
        return all_permissions()

    m = conn.execute(select(t("tenant_members").c.role_id, t("tenant_members").c.role)
                     .where(t("tenant_members").c.user_id == user_id,
                            t("tenant_members").c.tenant_id == tenant_id)).mappings().first()
    if m is None:
        return set()                                    # not a member of this org at all

    if m["role_id"]:
        rp = t("role_permissions")
        return {(r["module"], r["action"]) for r in conn.execute(
            select(rp.c.module, rp.c.action).where(rp.c.role_id == m["role_id"])).mappings()}

    # legacy membership: behave as the equivalent system role
    spec = SYSTEM_ROLES.get(LEGACY_ROLE_MAP.get(m["role"], "Viewer"))
    return spec["permissions"]() if spec else set()


def load_permissions(user: Principal) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        return permissions_for(conn, user.user_id, user.tenant_id)


def require(module: str, action: str):
    """Dependency factory: 403 unless the caller holds this permission.

    Usage:  user: Principal = Depends(require("documents", "publish"))
    """
    if action not in MODULES.get(module, ()):
        raise ValueError(f"unknown permission {module}.{action}")   # fails at import time

    def _dep(user: Principal = Depends(get_current_user)) -> Principal:
        if (module, action) not in load_permissions(user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"You do not have permission to {action} {MODULE_LABELS.get(module, module)}.")
        return user

    return _dep
