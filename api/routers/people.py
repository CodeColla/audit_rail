"""People register (Sprint 1 / M8) — the first thing entered into audit_rail.

Everything downstream needs an owner, so this comes before every register.

The load-bearing decision (D-SIGN): **a person does not need a login.** `user_id`
is nullable, so CMS/field engineers exist as records and attest to policies via an
emailed magic link instead of an account. `email` IS required — it's the channel
the signing link travels down, so a person without one could never attest at all.
"""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import func, insert, select, text, update

from api import activity
from api.auth import Principal, get_current_user, require_roles
from api.database import engine, get_conn, t
from api.util import IsoDate, now_iso

router = APIRouter(prefix="/people", tags=["people"])


class PersonIn(BaseModel):
    full_name: str
    email: str
    employee_number: str | None = None
    department: str | None = None
    position: str | None = None
    manager_id: str | None = None
    contract_start_date: IsoDate = None
    contract_end_date: IsoDate = None
    user_id: str | None = None          # optional — most people never get a login

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        # the email_addr domain requires lowercase and an '@' with text either side
        v = (v or "").strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("a valid email address is required")
        return v


class PersonPatch(BaseModel):
    full_name: str | None = None
    employee_number: str | None = None
    department: str | None = None
    position: str | None = None
    manager_id: str | None = None
    contract_start_date: IsoDate = None
    contract_end_date: IsoDate = None
    state: str | None = None


def _effective(row: dict, today: str) -> str:
    """Contract dates are authoritative over the stored flag (mirrors
    v_people_effective_state, which Core queries can't see — views=False)."""
    if row.get("state") == "INACTIVE":
        return "INACTIVE"
    end = row.get("contract_end_date")
    if end and str(end)[:10] < today:
        return "INACTIVE"
    return "ACTIVE"


@router.get("")
def list_people(
    department: str | None = Query(None),
    state: str | None = Query(None, pattern="^(ACTIVE|INACTIVE)$"),
    q: str | None = Query(None, description="search name / email / employee number"),
    user: Principal = Depends(get_current_user),
    conn=Depends(get_conn),
):
    from api.util import today_iso
    p = t("people")
    stmt = select(p).where(p.c.tenant_id == user.tenant_id).order_by(p.c.full_name)
    if department:
        stmt = stmt.where(p.c.department == department)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(p.c.full_name).like(like)
                          | func.lower(p.c.email).like(like)
                          | func.lower(func.coalesce(p.c.employee_number, "")).like(like))
    today = today_iso()
    rows = [{**dict(r), "effective_state": _effective(dict(r), today)}
            for r in conn.execute(stmt).mappings()]
    if state:
        rows = [r for r in rows if r["effective_state"] == state]
    return rows


@router.get("/departments")
def list_departments(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    """Distinct departments in use. There is no `departments` table — department is
    free text on people, so the filter list is derived."""
    p = t("people")
    rows = conn.execute(
        select(p.c.department, func.count())
        .where(p.c.tenant_id == user.tenant_id, p.c.department.isnot(None))
        .group_by(p.c.department).order_by(p.c.department)).all()
    return [{"department": d, "count": n} for d, n in rows]


@router.get("/org-chart")
def org_chart(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    """Manager tree — VRA #3.1 ('organizational charts')."""
    from api.util import today_iso
    p = t("people")
    rows = [dict(r) for r in conn.execute(
        select(p.c.id, p.c.full_name, p.c.position, p.c.department, p.c.manager_id,
               p.c.state, p.c.contract_end_date)
        .where(p.c.tenant_id == user.tenant_id).order_by(p.c.full_name)).mappings()]
    today = today_iso()
    by_id = {r["id"]: {**r, "effective_state": _effective(r, today), "reports": []}
             for r in rows}
    roots = []
    for r in by_id.values():
        parent = by_id.get(r["manager_id"]) if r["manager_id"] else None
        (parent["reports"] if parent else roots).append(r)
    return {"roots": roots, "total": len(rows)}


@router.get("/{person_id}")
def person_detail(person_id: str, user: Principal = Depends(get_current_user),
                  conn=Depends(get_conn)):
    from api.util import today_iso
    p = t("people")
    row = conn.execute(select(p).where(
        p.c.id == person_id, p.c.tenant_id == user.tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "person not found")
    d = dict(row)
    mgr = None
    if d["manager_id"]:
        mgr = conn.execute(select(p.c.id, p.c.full_name).where(
            p.c.id == d["manager_id"])).mappings().first()
    reports = [dict(r) for r in conn.execute(
        select(p.c.id, p.c.full_name, p.c.position)
        .where(p.c.manager_id == person_id)).mappings()]
    return {**d, "effective_state": _effective(d, today_iso()),
            "manager": dict(mgr) if mgr else None, "reports": reports,
            "has_login": d["user_id"] is not None}


@router.post("", status_code=201)
def create_person(body: PersonIn, user: Principal = Depends(get_current_user)):
    pid, now = str(uuid.uuid4()), now_iso()
    with engine.begin() as conn:
        try:
            conn.execute(insert(t("people")).values(
                id=pid, tenant_id=user.tenant_id, **body.model_dump(),
                state="ACTIVE", source="MANUAL", created_at=now, updated_at=now))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, _friendly(e))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="person.created", entity_type="person", entity_id=pid,
                 detail={"full_name": body.full_name})
    return {"id": pid}


@router.patch("/{person_id}")
def update_person(person_id: str, body: PersonPatch,
                  user: Principal = Depends(get_current_user)):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    if vals.get("manager_id") == person_id:
        raise HTTPException(400, "a person cannot manage themselves")
    vals["updated_at"] = now_iso()
    p = t("people")
    with engine.begin() as conn:
        if conn.execute(select(p.c.id).where(
                p.c.id == person_id, p.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "person not found")
        try:
            conn.execute(update(p).where(p.c.id == person_id).values(**vals))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, _friendly(e))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="person.updated", entity_type="person", entity_id=person_id,
                 detail=vals)
    return {"ok": True}


CSV_COLUMNS = ("full_name", "email", "employee_number", "department", "position",
               "contract_start_date", "contract_end_date")


@router.post("/import")
async def import_people(
    file: UploadFile = File(...),
    user: Principal = Depends(require_roles("admin", "manager")),
):
    """CSV → people (source=IMPORT). Bad rows are REPORTED, never silently dropped."""
    try:
        text_data = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "file must be UTF-8 encoded CSV")
    reader = csv.DictReader(io.StringIO(text_data))
    if not reader.fieldnames or "full_name" not in reader.fieldnames:
        raise HTTPException(400,
                            f"CSV needs a header row with at least: full_name, email. "
                            f"Recognised columns: {', '.join(CSV_COLUMNS)}")

    created, errors = 0, []
    now = now_iso()
    with engine.begin() as conn:
        for i, raw in enumerate(reader, start=2):    # row 1 is the header
            data = {k: (raw.get(k) or "").strip() or None for k in CSV_COLUMNS}
            try:
                person = PersonIn(**{k: v for k, v in data.items() if v is not None})
            except Exception as e:  # noqa: BLE001
                errors.append({"row": i, "name": data.get("full_name"),
                               "error": _pydantic_msg(e)})
                continue
            try:
                with conn.begin_nested():            # one bad row must not kill the batch
                    conn.execute(insert(t("people")).values(
                        id=str(uuid.uuid4()), tenant_id=user.tenant_id,
                        **person.model_dump(), state="ACTIVE", source="IMPORT",
                        created_at=now, updated_at=now))
                created += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"row": i, "name": data.get("full_name"),
                               "error": _friendly(e)})
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="people.imported", entity_type="person", entity_id=None,
                 detail={"created": created, "errors": len(errors)})
    return {"created": created, "failed": len(errors), "errors": errors}


def _friendly(e: Exception) -> str:
    """Turn Postgres constraint noise into something a human can act on."""
    s = str(getattr(e, "orig", e))
    if "people_tenant_id_email_key" in s or "uq_people" in s or "duplicate key" in s and "email" in s:
        return "that email is already on the register"
    if "email_addr" in s:
        return "invalid email address"
    if "people_no_self_manage" in s:
        return "a person cannot manage themselves"
    if "people_manager_id_tenant_id_fkey" in s:
        return "manager not found in this organisation"
    if "people_user_id_tenant_id_fkey" in s:
        return "that login is not a member of this organisation"
    if "uq_people_user" in s:
        return "that login is already linked to another person"
    return s.split("\n")[0][:200]


def _pydantic_msg(e: Exception) -> str:
    errs = getattr(e, "errors", None)
    if callable(errs):
        try:
            return "; ".join(f"{'.'.join(str(x) for x in d['loc'])}: {d['msg']}"
                             for d in errs())
        except Exception:  # noqa: BLE001
            pass
    return str(e)[:200]
