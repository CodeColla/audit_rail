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
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import func, insert, select, text, update

from api import activity, importer, xlsx_io
from api.auth import Principal, get_current_user
from api.permissions import require
from api.database import engine, get_conn, t
from api.util import IsoDate, StrictModel, now_iso

router = APIRouter(prefix="/people", tags=["people"])


class PersonIn(StrictModel):
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


class PersonPatch(StrictModel):
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
    user: Principal = Depends(require("people", "view")),
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
def list_departments(user: Principal = Depends(require("people", "view")), conn=Depends(get_conn)):
    """Distinct departments in use. There is no `departments` table — department is
    free text on people, so the filter list is derived."""
    p = t("people")
    rows = conn.execute(
        select(p.c.department, func.count())
        .where(p.c.tenant_id == user.tenant_id, p.c.department.isnot(None))
        .group_by(p.c.department).order_by(p.c.department)).all()
    return [{"department": d, "count": n} for d, n in rows]


@router.get("/org-chart")
def org_chart(user: Principal = Depends(require("people", "view")), conn=Depends(get_conn)):
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
def person_detail(person_id: str, user: Principal = Depends(require("people", "view")),
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
def create_person(body: PersonIn, user: Principal = Depends(require("people", "add"))):
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


class InviteIn(StrictModel):
    role_id: str | None = None          # defaults to the Viewer system role


@router.post("/{person_id}/invite", status_code=201)
def invite_person(person_id: str, body: InviteIn,
                  user: Principal = Depends(require("users", "add"))):
    """Give a person a login.

    Creates the user in `invited` state with NO password — they set their own via the
    returned link — plus a membership carrying the chosen role, and bridges the two through
    the existing `people.user_id` column. Staff who never sign in still attest by magic
    link, so this is additive.

    The link is returned in the response (there is no mailer yet — same copy-the-link
    approach as attestation).
    """
    from api.routers.auth import issue_invite

    p, users = t("people"), t("users")
    with engine.begin() as conn:
        person = conn.execute(select(p).where(
            p.c.id == person_id, p.c.tenant_id == user.tenant_id)).mappings().first()
        if person is None:
            raise HTTPException(404, "person not found")
        if person["user_id"]:
            raise HTTPException(409, f"{person['full_name']} already has a login")

        role_id = body.role_id
        if role_id is None:
            role_id = conn.execute(select(t("roles").c.id).where(
                t("roles").c.tenant_id == user.tenant_id,
                t("roles").c.name == "Viewer")).scalar()
        elif conn.execute(select(t("roles").c.id).where(
                t("roles").c.id == role_id,
                t("roles").c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "that role is not in this organisation")

        # users.email is globally unique — an existing login is attached, not duplicated
        uid = conn.execute(select(users.c.id).where(users.c.email == person["email"])).scalar()
        if uid is None:
            uid = str(uuid.uuid4())
            conn.execute(insert(users).values(
                id=uid, email=person["email"], full_name=person["full_name"],
                auth_provider="local", is_platform_admin=0, status="invited",
                created_at=now_iso()))

        already = conn.execute(select(t("tenant_members").c.id).where(
            t("tenant_members").c.tenant_id == user.tenant_id,
            t("tenant_members").c.user_id == uid)).scalar()
        if not already:
            conn.execute(insert(t("tenant_members")).values(
                id=str(uuid.uuid4()), tenant_id=user.tenant_id, user_id=uid,
                role="member", role_id=role_id, created_at=now_iso()))
        # the composite FK requires the membership to exist first
        conn.execute(update(p).where(p.c.id == person_id).values(user_id=uid))
        raw = issue_invite(conn, tenant_id=user.tenant_id, user_id=uid,
                           invited_by=user.user_id)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="person.invited", entity_type="person", entity_id=person_id,
                 detail={"email": person["email"]})
    return {"user_id": uid, "invite_path": f"/accept-invite/{raw}", "token": raw}


@router.patch("/{person_id}")
def update_person(person_id: str, body: PersonPatch,
                  user: Principal = Depends(require("people", "edit"))):
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


# ------------------------------------------------------------------ delete (P5-S5)

#: Postgres FK delete rules that REFUSE the parent delete. 'r' = RESTRICT, 'a' = NO ACTION
#: (the default when no ON DELETE is written, and it blocks just the same). 'n' (SET NULL)
#: and 'c' (CASCADE) resolve themselves and are deliberately absent.
_BLOCKING_FK_RULES = ("r", "a")

_REFERRERS_SQL = text("""
SELECT src.relname AS table_name, att.attname AS column_name
  FROM pg_constraint con
  JOIN pg_class src ON src.oid = con.conrelid
  JOIN pg_class tgt ON tgt.oid = con.confrelid
  JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
  JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
 WHERE con.contype = 'f'
   AND tgt.relname = 'people'
   AND att.attname <> 'tenant_id'
   AND con.confdeltype = ANY(:rules)
 ORDER BY src.relname, att.attname
""")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

#: `table_name` -> what a user calls it. Anything unlisted falls back to the table name.
_REFERRER_LABELS = {
    "risks": "risk", "assets": "asset", "data_items": "data item", "controls": "control",
    "incidents": "incident", "tasks": "task", "documents": "document",
    "third_parties": "third party", "obligations": "obligation", "findings": "finding",
    "trainings": "training", "training_assignments": "training assignment",
    "document_signatures": "signed document", "electronic_signatures": "signature",
    "document_approval_decisions": "approval decision",
    "access_review_decisions": "access review decision",
    "access_review_entries": "access review entry",
    "trust_center_document_access": "trust-centre grant",
}


def _person_blockers(conn, tenant_id: str, person_id: str) -> list[str]:
    """Which records still point at this person through a FK that would refuse the delete.

    Derived from the Postgres catalog rather than a hand-written list. There are 20+ such
    columns today and the schema keeps growing; a hardcoded list would silently rot the
    first time someone adds a FK, and the failure mode is a raw 500 on delete. This way a
    new referrer is covered the day it is created.

    Identifiers come from the catalog, never from user input, but they are still checked
    against `_IDENT` before being interpolated — a table name reaching an f-string is
    exactly the shape of bug worth refusing to write.
    """
    blockers: dict[str, int] = {}
    for row in conn.execute(_REFERRERS_SQL, {"rules": list(_BLOCKING_FK_RULES)}).mappings():
        table, column = row["table_name"], row["column_name"]
        if not (_IDENT.match(table) and _IDENT.match(column)):   # pragma: no cover - defence
            continue
        n = conn.execute(text(
            f"SELECT count(*) FROM {table} WHERE {column} = :p AND tenant_id = :t"),
            {"p": person_id, "t": tenant_id}).scalar() or 0
        if n:
            label = _REFERRER_LABELS.get(table, table.replace("_", " "))
            blockers[label] = blockers.get(label, 0) + n
    return [f"{n} {label}{'' if n == 1 else 's'}" for label, n in sorted(blockers.items())]


@router.delete("/{person_id}")
def delete_person(person_id: str, user: Principal = Depends(require("people", "delete"))):
    """Really delete a person — Sumit's decision — but refuse while anything still cites them.

    A soft "departed" state already exists (`v_people_effective_state`) and is the right tool
    for someone who has left. This is for a genuine mistake: a duplicate, or a typo'd row
    created minutes ago. Blocking-when-referenced is what keeps that from silently rewriting
    history, and the 409 names what to fix rather than saying "cannot delete".
    """
    p = t("people")
    with engine.begin() as conn:
        if conn.execute(select(p.c.id).where(
                p.c.id == person_id, p.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(404, "person not found")

        blockers = _person_blockers(conn, user.tenant_id, person_id)
        if blockers:
            raise HTTPException(409, "this person is still referenced by "
                                     + ", ".join(blockers)
                                     + " — reassign those first, or mark them a leaver instead")
        # `people.manager_id` is ON DELETE SET NULL, so reports are orphaned, not blocked.
        conn.execute(p.delete().where(p.c.id == person_id, p.c.tenant_id == user.tenant_id))

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="person.deleted", entity_type="person", entity_id=person_id, detail={})
    return {"ok": True}


CSV_COLUMNS = ("full_name", "email", "employee_number", "department", "position",
               "contract_start_date", "contract_end_date")


@router.post("/import")
async def import_people(
    file: UploadFile = File(...),
    user: Principal = Depends(require("people", "add")),
):
    """CSV **or .xlsx** -> people (source=IMPORT). Bad rows are REPORTED, never silently dropped.

    P5-S5 moved this onto the shared `api.importer` loop and the shared `xlsx_io.read_rows`,
    so it gained xlsx support and now fails rows identically to the register importers. The
    header contract is unchanged — the snake_case names in `CSV_COLUMNS`, not the register
    importers' human labels — because customers already have files in that shape and
    renaming their columns to tidy up our code would be a poor trade.
    """
    raw = await file.read()
    try:
        headers, rows = xlsx_io.read_rows(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception:                                            # noqa: BLE001
        raise HTTPException(400, "could not read that file — is it a .csv or .xlsx?") from None

    if "full_name" not in headers:
        raise HTTPException(400,
                            f"the file needs a header row with at least: full_name, email. "
                            f"Recognised columns: {', '.join(CSV_COLUMNS)}")
    if not rows:
        raise HTTPException(400, "that file has a header row but no data rows")

    def build(mapped: dict, _resolver) -> dict:
        data = {k: v for k, v in mapped.items() if v is not None}
        try:
            person = PersonIn(**data)
        except Exception as e:                                   # noqa: BLE001
            raise importer.RowError(_pydantic_msg(e)) from e
        return {**person.model_dump(), "state": "ACTIVE", "source": "IMPORT"}

    with engine.begin() as conn:
        result = importer.import_rows(
            conn, tenant_id=user.tenant_id, table="people", rows=rows,
            mapping={c: c for c in CSV_COLUMNS},     # identity: our headers are the field names
            build=build, label_key="full_name", friendly=_friendly)

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="people.imported", entity_type="person", entity_id=None,
                 detail={"created": result["created"], "errors": result["failed"]})
    return result


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
