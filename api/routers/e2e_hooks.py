"""Test-only hooks for the Playwright suite.

NOT MOUNTED unless the environment sets `E2E_TEST_HOOKS=1`, which only
`webui/playwright.config.ts` does. There is no way to reach these in dev or production —
`api/main.py` never imports the router otherwise.

They exist because a few states are unreachable from a browser: you cannot make a password
30 days old by clicking. Everything here still requires a valid session and only ever
touches the CALLER's own rows.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from api.core.auth import Principal, get_current_user
from api.core.database import engine
from api.core.util import StrictModel

router = APIRouter(prefix="/e2e", tags=["e2e-hooks"])


@router.post("/age-password")
def age_password(days: int = 31, user: Principal = Depends(get_current_user)):
    """Backdate the caller's current password so the 30-day expiry has elapsed."""
    when = (dt.date.today() - dt.timedelta(days=days)).isoformat() + "T00:00:00Z"
    with engine.begin() as conn:
        conn.execute(text("UPDATE user_password_history SET changed_at = :d "
                          "WHERE user_id = :u AND level = 0"),
                     {"d": when, "u": user.user_id})
    return {"ok": True, "changed_at": when}


class MakeMemberIn(StrictModel):
    email: str
    full_name: str
    role_name: str
    password: str


@router.post("/make-member", status_code=201)
def make_member(body: MakeMemberIn, user: Principal = Depends(get_current_user)):
    """Add a login with a given role to the CALLER'S organisation.

    Stands in for the People invite UI, which lands in a later sprint — without it a browser
    test cannot produce a non-admin session to check permission gating against.
    """
    from api.domain.passwords import set_password

    with engine.begin() as conn:
        rid = conn.execute(text("SELECT id FROM roles WHERE tenant_id=:t AND name=:n"),
                           {"t": user.tenant_id, "n": body.role_name}).scalar()
        if rid is None:
            raise HTTPException(400, f"no role named {body.role_name!r} in this organisation")
        uid = str(uuid.uuid4())
        conn.execute(text("INSERT INTO users (id,email,full_name,auth_provider,"
                          "is_platform_admin,status) VALUES (:i,:e,:f,'local',0,'active')"),
                     {"i": uid, "e": body.email.lower().strip(), "f": body.full_name})
        conn.execute(text("INSERT INTO tenant_members (id,tenant_id,user_id,role,role_id) "
                          "VALUES (:i,:t,:u,'member',:r)"),
                     {"i": str(uuid.uuid4()), "t": user.tenant_id, "u": uid, "r": rid})
        set_password(conn, uid, body.password)
    return {"user_id": uid}
