"""Dropdown vocabularies — read by every form, edited by admins (P4-S3).

Reads are open to any member: a Viewer still needs the category list to render a record's
category, and an Editor needs it to fill a form. Writes are an organisation-settings action.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, insert, select, update

from api.domain import vocabularies
from api.core.auth import Principal, get_current_user
from api.core.database import engine, get_conn, t
from api.core.permissions import require
from api.core.util import StrictModel, now_iso

router = APIRouter(prefix="/lookups", tags=["lookups"])


def _check_kind(kind: str) -> None:
    if kind not in vocabularies.KINDS:
        raise HTTPException(400, f"unknown vocabulary {kind!r}")


@router.get("")
def list_lookups(kind: str | None = Query(None), include_inactive: bool = Query(False),
                 user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    """All vocabularies, or one. Grouped by kind so a form can pick what it needs."""
    lv = t("lookup_values")
    stmt = select(lv).where(lv.c.tenant_id == user.tenant_id).order_by(
        lv.c.kind, lv.c.sort_order, lv.c.value)
    if kind:
        _check_kind(kind)
        stmt = stmt.where(lv.c.kind == kind)
    if not include_inactive:
        stmt = stmt.where(lv.c.is_active == 1)
    out: dict[str, list] = {k: [] for k in (
        [kind] if kind else vocabularies.KINDS)}
    for r in conn.execute(stmt).mappings():
        out.setdefault(r["kind"], []).append(dict(r))
    return {"kinds": {k: {"label": vocabularies.KINDS[k][0], "values": v}
                      for k, v in out.items() if k in vocabularies.KINDS}}


class LookupIn(StrictModel):
    kind: str
    value: str
    sort_order: int = 0


@router.post("", status_code=201)
def create_lookup(body: LookupIn, user: Principal = Depends(require("org", "edit"))):
    _check_kind(body.kind)
    value = (body.value or "").strip()
    if not value:
        raise HTTPException(400, "a value is required")
    lv = t("lookup_values")
    with engine.begin() as conn:
        if conn.execute(select(lv.c.id).where(
                lv.c.tenant_id == user.tenant_id, lv.c.kind == body.kind,
                lv.c.value == value)).first():
            raise HTTPException(409, f"{value!r} is already in that list")
        lid = str(uuid.uuid4())
        conn.execute(insert(lv).values(
            id=lid, tenant_id=user.tenant_id, kind=body.kind, value=value,
            sort_order=body.sort_order, is_active=1, created_at=now_iso()))
    return {"id": lid}


class LookupPatch(StrictModel):
    value: str | None = None
    sort_order: int | None = None
    is_active: int | None = None


@router.patch("/{lookup_id}")
def update_lookup(lookup_id: str, body: LookupPatch,
                  user: Principal = Depends(require("org", "edit"))):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    if "value" in vals:
        vals["value"] = vals["value"].strip()
        if not vals["value"]:
            raise HTTPException(400, "a value is required")
    lv = t("lookup_values")
    with engine.begin() as conn:
        if conn.execute(select(lv.c.id).where(
                lv.c.id == lookup_id, lv.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "value not found")
        conn.execute(update(lv).where(lv.c.id == lookup_id).values(**vals))
    return {"ok": True}


@router.delete("/{lookup_id}")
def delete_lookup(lookup_id: str, user: Principal = Depends(require("org", "edit"))):
    """Remove a value outright.

    Records already carrying it keep their text — the column is free text underneath, so
    nothing breaks; the value simply stops being offered. Deactivating (is_active=0) is the
    gentler option and is what the UI offers first.
    """
    lv = t("lookup_values")
    with engine.begin() as conn:
        if conn.execute(select(lv.c.id).where(
                lv.c.id == lookup_id, lv.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "value not found")
        conn.execute(delete(lv).where(lv.c.id == lookup_id))
    return {"ok": True}
