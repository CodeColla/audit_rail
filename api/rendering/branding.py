"""The organisation's identity as it appears on an exported document (P6-S5).

One function, in its own module, because both export paths need it and neither should learn
SQL: `api/render.py` is a pure renderer with no database access at all, and `api/docx_export.py`
is a pure HTML->Word walker. The router looks the facts up and hands them down.

**This fixes a real multi-tenant defect, not a cosmetic one.** Before P6-S5, `render_pdf()` had
no `org` parameter and `routers/documents.py` never read `tenants.name`, so every customer's
approved and signed policy was letterheaded `render.DEFAULT_ORG` — the first customer's company
name. `DEFAULT_ORG` survives only as the fallback for a tenant row that cannot be read.

The logo is read straight off local disk. `tenants.logo_file_id` references `files (id,
tenant_id)` (db/schema.sql), so the database itself guarantees the blob belongs to this
organisation — this module never has to be trusted to scope it.
"""

from __future__ import annotations

import base64

from sqlalchemy import select

from api.rendering import imagefile, render
from api.core import storage
from api.core.database import t


def letterhead(conn, tenant_id: str) -> dict:
    """`{"org", "logo", "logo_mime", "logo_data_uri"}` for one tenant.

    The logo comes back in both forms because the two exports need different ones and neither
    should have to convert:

    * **`logo_data_uri` for the PDF.** xhtml2pdf routes a `data:` src to `B64InlineURI`, which
      is a plain `base64.b64decode` — no filesystem, no network (only `http`/`https` reach
      `urlopen`). Handing it a filesystem path would also work and would mean a rendering path
      that opens files named by a string, which is the shape of a local-file-inclusion bug for
      no benefit.
    * **`logo` bytes for the DOCX,** because `add_picture` wants a stream.

    Never raises. A missing tenant row, an unset logo, or a `files` row whose blob has gone
    from disk all degrade to "no logo" — an export is not the place to discover a storage
    problem, and a policy without a logo is still a valid policy.
    """
    tenants, files = t("tenants"), t("files")
    row = conn.execute(
        select(tenants.c.name, files.c.storage_key, files.c.mime_type)
        .select_from(tenants.outerjoin(files, tenants.c.logo_file_id == files.c.id))
        .where(tenants.c.id == tenant_id)).mappings().first()
    out = {"org": render.DEFAULT_ORG, "logo": None, "logo_mime": None,
           "logo_data_uri": None, "width": None, "height": None}
    if row is None:
        return out

    out["org"] = row["name"] or render.DEFAULT_ORG
    if row["storage_key"]:
        try:
            data = storage.path_for(row["storage_key"]).read_bytes()
            mime = row["mime_type"] or "image/png"
            out["logo"], out["logo_mime"] = data, mime
            out["logo_data_uri"] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            # The PDF letterhead MUST size the logo explicitly — see `render._letterhead_html`.
            # An unsized image renders at its intrinsic pixel size, overflows the static header
            # frame, and xhtml2pdf then silently drops the ENTIRE frame, so the document loses
            # its letterhead altogether with no error anywhere.
            out.update(imagefile.dimensions(data))
        except (OSError, ValueError):
            # ValueError is storage.path_for's containment guard; OSError is a vanished blob.
            pass
    return out
