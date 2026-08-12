"""Organisation identity — the logo shown in the header and sidebar (P6).

Small on purpose. The only genuinely interesting decisions here are about what a customer is
allowed to upload, and they are security decisions rather than product ones:

* **SVG is refused.** An SVG is executable markup — it can carry `<script>`, external
  references and event handlers — and this file is rendered inside the authenticated app on
  every screen. Sanitising one is possible (`api/html_sanitize.py` exists for exactly this
  class of problem) but a logo does not need vector fidelity, so the cheaper and safer answer
  is a raster format. Refused with a reason, not silently dropped.
* **The magic bytes are checked, not the filename or the browser's `Content-Type`.** Both of
  those are attacker-controlled; the first few bytes of the file are not.
* **The composite FK does the tenant scoping.** `tenants.logo_file_id` references
  `files (id, tenant_id)`, so the database itself refuses to let one organisation point at
  another's upload — see `db/schema.sql`. This route never has to be trusted to get it right.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import insert, select, update

from api import activity, imagefile, storage
from api.auth import Principal, get_current_user
from api.database import engine, get_conn, t
from api.imagefile import MAX_LOGO_MB
from api.permissions import require
from api.util import now_iso

router = APIRouter(prefix="/org", tags=["org"])

# P6-S5: the magic-byte sniffer and the size caps moved to `api/imagefile.py` when document
# images started needing exactly the same gate. Nothing about the behaviour changed — the same
# formats, the same refusals, the same 400 messages — so `tests/test_org_logo.py` still passes
# unedited, which is what proves the move was a move and not a rewrite.
_sniff = imagefile.sniff


@router.put("/logo")
async def set_logo(file: UploadFile = File(...),
                   user: Principal = Depends(require("org", "edit"))):
    data = await file.read()
    if not data:
        raise HTTPException(400, "that file is empty")
    if len(data) > MAX_LOGO_MB * 1024 * 1024:
        raise HTTPException(413, f"a logo must be under {MAX_LOGO_MB} MB")
    mime = _sniff(data)

    name = file.filename or "logo"
    key, sha, size = storage.save(user.tenant_id, data, name)
    file_id, now = str(uuid.uuid4()), now_iso()
    tenants, files, members = t("tenants"), t("files"), t("tenant_members")

    with engine.begin() as conn:
        member_id = conn.execute(select(members.c.id).where(
            members.c.tenant_id == user.tenant_id,
            members.c.user_id == user.user_id)).scalar()
        conn.execute(insert(files).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key, original_name=name,
            # the SNIFFED type, not the one the browser claimed
            mime_type=mime, size_bytes=size, sha256=sha,
            uploaded_by_member_id=member_id, created_at=now))
        # The previous logo's `files` row is deliberately left in place: it may be referenced
        # by nothing now, but deleting a vault object on a cosmetic change is a worse failure
        # than leaving a few kilobytes behind, and it keeps this route free of blob deletion.
        conn.execute(update(tenants).where(tenants.c.id == user.tenant_id)
                     .values(logo_file_id=file_id))

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="org.logo_set", entity_type="tenant", entity_id=user.tenant_id,
                 detail={"mime_type": mime, "size_bytes": size})
    return {"ok": True, "mime_type": mime, "size_bytes": size}


@router.delete("/logo")
def clear_logo(user: Principal = Depends(require("org", "edit"))):
    with engine.begin() as conn:
        conn.execute(update(t("tenants")).where(t("tenants").c.id == user.tenant_id)
                     .values(logo_file_id=None))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="org.logo_cleared", entity_type="tenant", entity_id=user.tenant_id,
                 detail={})
    return {"ok": True}


@router.get("/logo")
def get_logo(user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    """Serve the logo inline, to any member of the organisation.

    **Deliberately gated on membership alone, not on `org.view`.** `org` is one of
    `ADMIN_MODULES` (`api/permissions.py`), so Viewers and Editors do not hold `org.view` —
    gating this route on it would mean only administrators ever saw the company logo and
    everybody else got a 403 and an initials box. The header is not an admin surface. An
    organisation's own mark is not a secret from its own staff.

    Still scoped to the caller's tenant: the query joins on `tenants.id == user.tenant_id`, so
    there is no id to tamper with in the first place.

    The client fetches this through `fetchBlob` rather than a bare `<img src>`, because the
    app authenticates with a bearer header and an `<img>` cannot send one.
    """
    tenants, files = t("tenants"), t("files")
    row = conn.execute(
        select(files.c.storage_key, files.c.mime_type, files.c.original_name)
        .select_from(tenants.join(files, tenants.c.logo_file_id == files.c.id))
        .where(tenants.c.id == user.tenant_id)).mappings().first()
    if row is None:
        # 204, not 404. "This organisation has no logo" is a SUCCESSFUL answer to "what is
        # the logo" — most organisations never upload one, so a 404 here meant a failed
        # request on every page load of the product, which is noise in the logs, noise in
        # devtools, and a real failure signal wasted. The smoke suite (01-smoke.spec.ts)
        # asserts no /api/ response >= 400 on any route, and correctly caught this.
        return Response(status_code=204)
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "logo missing from storage")
    return FileResponse(
        path, media_type=row["mime_type"] or "image/png",
        headers={"Content-Disposition": 'inline; filename="logo"',
                 # revalidate rather than cache hard: a logo change must show up on reload,
                 # and the file is small enough that a 304 round-trip costs nothing.
                 "Cache-Control": "no-cache"})
