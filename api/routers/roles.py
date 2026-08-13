"""Roles & permissions administration (P4-S2).

A role is a named set of (module, action) pairs scoped to one organisation. The vocabulary
comes from api/permissions.MODULES, which is also what the UI renders as a checkbox matrix.

System roles (Admin / Editor / Viewer) are seeded into every organisation. They can be
inspected and assigned but not renamed or deleted, so an admin cannot lock everyone out by
editing the role they themselves depend on.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, insert, select, update

from api.core import activity
from api.core.auth import Principal
from api.core.database import engine, get_conn, t
from api.core.permissions import MODULE_LABELS, MODULES, require
from api.core.util import StrictModel, now_iso

router = APIRouter(prefix="/roles", tags=["roles"])


def _validate(perms: list[str]) -> set[tuple[str, str]]:
    """"module.action" strings -> pairs, rejecting anything outside the vocabulary."""
    out: set[tuple[str, str]] = set()
    for p in perms or []:
        module, _, action = str(p).partition(".")
        if action not in MODULES.get(module, ()):
            raise HTTPException(400, f"unknown permission {p!r}")
        out.add((module, action))
    return out


def _load(conn, tenant_id: str, role_id: str) -> dict:
    row = conn.execute(select(t("roles")).where(
        t("roles").c.id == role_id,
        t("roles").c.tenant_id == tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "role not found")
    return dict(row)


@router.get("/vocabulary")
def vocabulary(user: Principal = Depends(require("roles", "view"))):
    """Everything the checkbox matrix needs — modules, their actions, and labels."""
    return {"modules": [{"key": m, "label": MODULE_LABELS.get(m, m), "actions": list(acts)}
                        for m, acts in MODULES.items()]}


@router.get("")
def list_roles(user: Principal = Depends(require("roles", "view")), conn=Depends(get_conn)):
    roles, rp, tm = t("roles"), t("role_permissions"), t("tenant_members")
    perms: dict[str, list[str]] = {}
    for r in conn.execute(select(rp.c.role_id, rp.c.module, rp.c.action)
                          .join(roles, roles.c.id == rp.c.role_id)
                          .where(roles.c.tenant_id == user.tenant_id)).mappings():
        perms.setdefault(r["role_id"], []).append(f"{r['module']}.{r['action']}")
    counts = dict(conn.execute(
        select(tm.c.role_id, func.count()).where(tm.c.tenant_id == user.tenant_id)
        .group_by(tm.c.role_id)).all())
    return [{**dict(r), "permissions": sorted(perms.get(r["id"], [])),
             "member_count": counts.get(r["id"], 0)}
            for r in conn.execute(select(roles).where(roles.c.tenant_id == user.tenant_id)
                                  .order_by(roles.c.is_system.desc(), roles.c.name)).mappings()]


class RoleIn(StrictModel):
    name: str
    description: str | None = None
    permissions: list[str] = []


@router.post("", status_code=201)
def create_role(body: RoleIn, user: Principal = Depends(require("roles", "add"))):
    pairs = _validate(body.permissions)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "a role needs a name")
    with engine.begin() as conn:
        if conn.execute(select(t("roles").c.id).where(
                t("roles").c.tenant_id == user.tenant_id, t("roles").c.name == name)).first():
            raise HTTPException(409, f"a role called {name!r} already exists")
        rid = str(uuid.uuid4())
        conn.execute(insert(t("roles")).values(
            id=rid, tenant_id=user.tenant_id, name=name, description=body.description,
            is_system=0, created_at=now_iso()))
        if pairs:
            conn.execute(insert(t("role_permissions")),
                         [{"role_id": rid, "module": m, "action": a} for m, a in sorted(pairs)])
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id, action="role.created",
                 entity_type="role", entity_id=rid, detail={"name": name})
    return {"id": rid}


class RolePatch(StrictModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


@router.patch("/{role_id}")
def update_role(role_id: str, body: RolePatch,
                user: Principal = Depends(require("roles", "edit"))):
    with engine.begin() as conn:
        row = _load(conn, user.tenant_id, role_id)
        if row["is_system"] and (body.name or body.permissions is not None):
            raise HTTPException(
                409, "system roles cannot be changed — copy one into a new role instead")
        vals = {k: v for k, v in
                {"name": body.name, "description": body.description}.items() if v is not None}
        if vals:
            conn.execute(update(t("roles")).where(t("roles").c.id == role_id).values(**vals))
        if body.permissions is not None:
            pairs = _validate(body.permissions)
            rp = t("role_permissions")
            conn.execute(delete(rp).where(rp.c.role_id == role_id))
            if pairs:
                conn.execute(insert(rp), [{"role_id": role_id, "module": m, "action": a}
                                          for m, a in sorted(pairs)])
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id, action="role.updated",
                 entity_type="role", entity_id=role_id)
    return {"ok": True}


@router.delete("/{role_id}")
def delete_role(role_id: str, user: Principal = Depends(require("roles", "delete"))):
    with engine.begin() as conn:
        row = _load(conn, user.tenant_id, role_id)
        if row["is_system"]:
            raise HTTPException(409, "system roles cannot be deleted")
        in_use = conn.execute(select(func.count()).select_from(t("tenant_members"))
                              .where(t("tenant_members").c.role_id == role_id)).scalar()
        if in_use:
            raise HTTPException(
                409, f"{in_use} member(s) still hold this role — reassign them first")
        conn.execute(delete(t("roles")).where(t("roles").c.id == role_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id, action="role.deleted",
                 entity_type="role", entity_id=role_id)
    return {"ok": True}
