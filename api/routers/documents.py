"""Documents & policy lifecycle (Sprint 2 / M9a).

The state machine, in one place:

    (create)──► v1.0 DRAFT ──submit(threshold,approvers)──► PENDING_APPROVAL
                    ▲                                             │
          reject ──┘                              all decisions in │
                                                                   ▼
                                          approvals ≥ threshold → round APPROVED
                                                                   │  (explicit)
                                                                   ▼  publish
                                                              v1.0 PUBLISHED  ──► PDF rendered
                                                                   │
                    edit → new DRAFT (bump minor/major, content copied)

Two things the DB enforces for us, not the app (see db/schema.sql):
  • assert_publish_approved — NO publish (major OR minor) without threshold approvals.
    This is the Probo fix: a minor bump can't silently ship unapproved content.
  • freeze_published_version — a PUBLISHED version's bytes can never change (M5).
"""

from __future__ import annotations

import difflib
import hashlib
import re
import secrets
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, insert, select, text, update

from api.core import activity, storage
from api.rendering import branding, docx_export, imagefile, render, xlsx_io
from api.domain import vocabularies
from api.core.auth import Principal
from api.rendering.html_sanitize import sanitize_document_html
from api.core.permissions import require
from api.core.database import engine, get_conn, t
from api.core.util import (IsoDate, StrictModel, add_months, now_iso, now_plus_days,
                      review_status, today_iso)

router = APIRouter(prefix="/documents", tags=["documents"])

OPEN = ("DRAFT", "PENDING_APPROVAL")


# ------------------------------------------------------------------ helpers

def _member_id(conn, tenant_id, user_id):
    m = t("tenant_members")
    return conn.execute(select(m.c.id).where(
        m.c.tenant_id == tenant_id, m.c.user_id == user_id)).scalar()


def _doc(conn, user: Principal, doc_id: str):
    d = t("documents")
    row = conn.execute(select(d).where(
        d.c.id == doc_id, d.c.tenant_id == user.tenant_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "document not found")
    return dict(row)


def _require_active(doc: dict) -> dict:
    """`documents.status` was consulted in exactly one place — the list filter — so an
    ARCHIVED document stayed fully editable, publishable, and could issue fresh 30-day
    attestation magic links. Call this after `_doc()` in every endpoint that changes a
    document's content or starts a new obligation on it. Reads (detail, render, diff,
    coverage) and cleanup (discard-draft, restoring via PATCH) intentionally do not call
    this — you must be able to view, export and un-archive an archived document."""
    if doc["status"] == "ARCHIVED":
        raise HTTPException(409, "this document is archived — restore it first")
    return doc


#: 'r' (RESTRICT) and 'a' (NO ACTION) are what would actually refuse a delete; 'c' (CASCADE)
#: resolves itself and is deliberately absent. Same convention as people.py's
#: _person_blockers, target relname swapped from 'people' to 'documents'.
_DOC_BLOCKING_FK_RULES = ("r", "a")

_DOC_REFERRERS_SQL = text("""
SELECT src.relname AS table_name, att.attname AS column_name
  FROM pg_constraint con
  JOIN pg_class src ON src.oid = con.conrelid
  JOIN pg_class tgt ON tgt.oid = con.confrelid
  JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
  JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
 WHERE con.contype = 'f'
   AND tgt.relname = 'documents'
   AND att.attname <> 'tenant_id'
   AND con.confdeltype = ANY(:rules)
 ORDER BY src.relname, att.attname
""")

_DOC_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

#: `table_name` -> what a user calls it. Anything unlisted falls back to the table name.
_DOC_REFERRER_LABELS = {
    "statements_of_applicability": "Statement of Applicability",
    "access_review_campaigns": "access review campaign",
    "trust_center": "trust centre NDA setting",
}


def _document_blockers(conn, tenant_id: str, doc_id: str) -> list[str]:
    """Which records still point at this document through a FK that would refuse a hard
    delete. Catalog-driven, not a hand-written list — see people.py's _person_blockers for
    the same reasoning: a hardcoded table list rots the first time a new RESTRICT lands.

    One thing a single-level catalog query cannot see, checked separately below:
    `document_versions.document_id` CASCADEs (so it never shows up here, correctly — 'c' is
    excluded from `_DOC_BLOCKING_FK_RULES`), but `evidence.document_version_id` is RESTRICT
    on THOSE rows. A hard delete would let Postgres cascade documents -> document_versions
    and then hit that RESTRICT mid-transaction — a real, reachable case (evidence citing a
    specific policy version), worth naming up front rather than surfacing as a raw
    IntegrityError.
    """
    blockers: dict[str, int] = {}
    for row in conn.execute(_DOC_REFERRERS_SQL, {"rules": list(_DOC_BLOCKING_FK_RULES)}).mappings():
        table, column = row["table_name"], row["column_name"]
        if not (_DOC_IDENT.match(table) and _DOC_IDENT.match(column)):  # pragma: no cover - defence
            continue
        n = conn.execute(text(
            f"SELECT count(*) FROM {table} WHERE {column} = :d AND tenant_id = :t"),
            {"d": doc_id, "t": tenant_id}).scalar() or 0
        if n:
            label = _DOC_REFERRER_LABELS.get(table, table.replace("_", " "))
            blockers[label] = blockers.get(label, 0) + n

    dv = t("document_versions")
    version_ids = list(conn.execute(select(dv.c.id).where(
        dv.c.document_id == doc_id, dv.c.tenant_id == tenant_id)).scalars())
    if version_ids:
        ev = t("evidence")
        n = conn.execute(select(func.count()).where(
            ev.c.document_version_id.in_(version_ids), ev.c.tenant_id == tenant_id)).scalar() or 0
        if n:
            blockers["evidence"] = blockers.get("evidence", 0) + n

    return [f"{n} {label}{'' if n == 1 else 's'}" for label, n in sorted(blockers.items())]


def _versions(conn, doc_id: str):
    dv = t("document_versions")
    return [dict(r) for r in conn.execute(
        select(dv).where(dv.c.document_id == doc_id)
        .order_by(dv.c.major.desc(), dv.c.minor.desc())).mappings()]


def _latest_approval(conn, version_id: str):
    """The most recent non-cancelled approval round for a version, with its tally."""
    ap, dec, ppl = t("document_approvals"), t("document_approval_decisions"), t("people")
    a = conn.execute(
        select(ap).where(ap.c.document_version_id == version_id,
                         ap.c.status != "CANCELLED")
        # id DESC is a deterministic tiebreaker: opened_at is second-resolution, so two
        # rounds on one version (reject→resubmit) can share it. Without this, guard and
        # trigger could pick *different* tied rows and disagree. (submit also cancels the
        # prior round, so in practice only one non-cancelled round survives — belt & braces.)
        .order_by(ap.c.opened_at.desc(), ap.c.id.desc()).limit(1)).mappings().first()
    if a is None:
        return None
    decisions = [dict(r) for r in conn.execute(
        select(dec, ppl.c.full_name)
        .join(ppl, dec.c.approver_person_id == ppl.c.id)
        .where(dec.c.approval_id == a["id"])
        .order_by(ppl.c.full_name)).mappings()]
    approved = sum(1 for d in decisions if d["state"] == "APPROVED")
    return {**dict(a), "decisions": decisions, "approved": approved,
            "can_publish": a["status"] == "APPROVED"}


def _disposition(disposition: str, filename: str) -> str:
    """A Content-Disposition header that survives a non-English document title.

    Starlette encodes response headers as latin-1, so interpolating a title raw meant any
    character above U+00FF — Devanagari, Tamil, a stray curly quote — raised
    UnicodeEncodeError and 500'd the download. RFC 5987 gives the real name via filename*
    and keeps an ASCII filename= for older clients.
    """
    clean = "".join(ch for ch in filename if ch.isprintable() and ch not in '"\\/').strip()
    ascii_name = clean.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return (f'{disposition}; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(clean, safe='')}")


def image_resolver(conn, tenant_id: str):
    """`file_id -> (bytes, mime)` for images this tenant owns, or `None`. Injected into
    `render.build_html` and `docx_export.render_docx` so neither has to import a database.

    **The tenant predicate is not belt-and-braces, it is the control.** The MARKDOWN branch of
    a document is stored UNSANITISED on purpose (`_clean` leaves it alone, because rewriting
    the bytes would move `content_sha256`, which backs the e-signature), so an author can
    hand-write `![](/api/documents/images/<another-tenant's-uuid>)` and have it arrive here.
    Looking the id up by itself would embed another organisation's bytes into a published,
    frozen, signed PDF. `purpose` does the same job across modules — see `db/schema.sql`.
    """
    files = t("files")

    def resolve(file_id: str):
        row = conn.execute(
            select(files.c.storage_key, files.c.mime_type).where(
                files.c.id == file_id, files.c.tenant_id == tenant_id,
                files.c.purpose == "DOC_IMAGE")).mappings().first()
        if row is None:
            return None
        try:
            return storage.path_for(row["storage_key"]).read_bytes(), \
                row["mime_type"] or "image/png"
        except (OSError, ValueError):
            # A vanished blob must degrade to "no image", never to a failed export.
            return None

    return resolve


def _render_and_store(conn, user: Principal, doc: dict, ver: dict) -> str:
    """Render the version to PDF, save it to the vault, return the new file_id."""
    brand = branding.letterhead(conn, user.tenant_id)
    pdf, _engine = render.render_pdf(
        title=doc["title"], body_md=ver["content"] or "",
        classification=doc["classification"], version_label=ver["version_label"],
        status="PUBLISHED", content_format=ver["content_format"],
        org=brand["org"], logo_data_uri=brand["logo_data_uri"],
        logo_w=brand["width"], logo_h=brand["height"],
        image_resolver=image_resolver(conn, user.tenant_id))
    key, sha, size = storage.save(user.tenant_id, pdf,
                                  f"{doc['title']}-v{ver['version_label']}.pdf")
    file_id = str(uuid.uuid4())
    member_id = _member_id(conn, user.tenant_id, user.user_id)
    conn.execute(insert(t("files")).values(
        id=file_id, tenant_id=user.tenant_id, storage_key=key,
        original_name=f"{doc['title']} v{ver['version_label']}.pdf",
        mime_type="application/pdf", size_bytes=size, sha256=sha,
        uploaded_by_member_id=member_id, created_at=now_iso()))
    return file_id


# ------------------------------------------------------------------ documents

class DocumentIn(StrictModel):
    title: str
    document_type: str = "POLICY"
    classification: str = "INTERNAL"
    owner_person_id: str
    description: str | None = None
    review_cadence_months: int | None = 12
    content: str | None = None          # optional starting body for v1.0
    content_format: str = "MARKDOWN"


def _clean(content: str | None, fmt: str) -> str | None:
    """Sanitise on the way in, but only what is actually HTML.

    Markdown must pass through untouched: nh3 would eat `a < b` and any literal angle
    bracket, and rewriting stored bytes changes content_sha256 — which
    electronic_signatures.file_sha256 pins.
    """
    if content is None:
        return None
    return sanitize_document_html(content) if fmt == "HTML" else content


def _check_format(fmt: str) -> str:
    if fmt not in ("MARKDOWN", "HTML", "SHEET"):
        raise HTTPException(400, "content_format must be MARKDOWN, HTML or SHEET")
    return fmt


def _validate_sheet(content: str | None, fmt: str) -> None:
    """A SHEET version's `content` must be the JSON `render.parse_sheet` can read — checked
    on every write, not just at export time, so a malformed grid is a clean 400 the moment
    it's saved, not a 500 the next time someone tries to publish or export it."""
    if fmt != "SHEET" or content is None:
        return
    try:
        render.parse_sheet(content)
    except render.SheetFormatError as e:
        raise HTTPException(400, f"invalid spreadsheet content: {e}") from e


# ------------------------------------------------------------------ inline images (P6-S5)

@router.post("/{doc_id}/images", status_code=201)
async def upload_document_image(doc_id: str, file: UploadFile = File(...),
                                user: Principal = Depends(require("documents", "edit"))):
    """Take an image into the vault so a document can reference it.

    The bytes are gated by `imagefile.sniff` — magic bytes, not the filename and not the
    browser's `Content-Type`, both of which the uploader controls — and the SNIFFED type is
    what gets stored. SVG is refused with a reason: it is executable markup, and this image is
    rendered inside the app and walked by the export code.

    `purpose='DOC_IMAGE'` is what lets the serving route below authorise safely; see the note
    on `files` in db/schema.sql for the cross-module read it prevents.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "that file is empty")
    if len(data) > imagefile.MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(413, f"an image must be under {imagefile.MAX_IMAGE_MB} MB")
    mime = imagefile.sniff(data)

    with engine.begin() as conn:
        _require_active(_doc(conn, user, doc_id))
        # The extension comes from the SNIFFED mime, never from the upload's filename: a
        # pasted screenshot arrives as "image.png" or nameless, and a hostile one arrives as
        # "invoice.pdf.png". `storage.save` takes its suffix from whatever it is handed.
        key, sha, size = storage.save(
            user.tenant_id, data, f"image{imagefile.EXTENSIONS.get(mime, '.bin')}")
        file_id = str(uuid.uuid4())
        conn.execute(insert(t("files")).values(
            id=file_id, tenant_id=user.tenant_id, storage_key=key,
            original_name=file.filename or "image", mime_type=mime,
            size_bytes=size, sha256=sha, purpose="DOC_IMAGE",
            uploaded_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now_iso()))

    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.image_added", entity_type="document", entity_id=doc_id,
                 detail={"file_id": file_id, "mime_type": mime, "size_bytes": size})
    # `url` is returned rather than assembled in the browser so exactly one place in the
    # system decides the shape `html_sanitize.IMG_SRC_RE` will accept.
    return {"file_id": file_id, "url": f"/api/documents/images/{file_id}",
            "mime_type": mime, "size_bytes": size, **imagefile.dimensions(data)}


# Declared before /{doc_id} on purpose — FastAPI matches in order, and otherwise "images"
# would be read as a document id.
@router.get("/images/{file_id}")
def get_document_image(file_id: str,
                       user: Principal = Depends(require("documents", "view")),
                       conn=Depends(get_conn)):
    """Serve one document image to a member of the tenant that owns it.

    **`purpose == 'DOC_IMAGE'` is load-bearing, not tidiness.** `files` is a shared vault:
    evidence uploads, contracts, asset photos, templates and published PDFs all live in it,
    and most of those routes store `file.content_type` — the value the browser claimed. Without
    this predicate, anyone holding `documents.view` could read any file in the tenant through
    this route, and an uploader could steer it by lying about a content type. With it, this
    route can only ever reach rows its own sniffing upload path created.

    404 rather than 403 for someone else's id: a 403 confirms the file exists.
    """
    files = t("files")
    row = conn.execute(
        select(files.c.storage_key, files.c.mime_type).where(
            files.c.id == file_id, files.c.tenant_id == user.tenant_id,
            files.c.purpose == "DOC_IMAGE")).mappings().first()
    if row is None:
        raise HTTPException(404, "image not found")
    path = storage.path_for(row["storage_key"])
    if not path.exists():
        raise HTTPException(410, "image missing from storage")
    # Cached hard, unlike the org logo: an image inside a version is immutable — a published
    # version is frozen, and a draft edit produces a new file id rather than new bytes.
    return FileResponse(path, media_type=row["mime_type"] or "image/png",
                        headers={"Content-Disposition": 'inline; filename="image"',
                                 "Cache-Control": "private, max-age=3600"})


@router.get("/types")
def document_types(user: Principal = Depends(require("documents", "view"))):
    """The document types the database will actually accept.

    Served rather than hardcoded in the SPA: a stale copy in the UI is how "STANDARD" —
    never a valid value — reached users as a 500 CheckViolation. Gated like every other
    read in this file — it was on bare authentication, so a member with no documents
    permission at all still got a 200.
    """
    return [{"value": v, "label": v.replace("_", " ").title()}
            for v in vocabularies.DOCUMENT_TYPES]


@router.get("")
def list_documents(document_type: str | None = Query(None), status: str | None = Query(None),
                   q: str | None = Query(None), include_archived: bool = Query(False),
                   user: Principal = Depends(require("documents", "view")), conn=Depends(get_conn)):
    d, ppl, dv = t("documents"), t("people"), t("document_versions")
    stmt = (select(d, ppl.c.full_name.label("owner_name"))
            .join(ppl, d.c.owner_person_id == ppl.c.id)
            .where(d.c.tenant_id == user.tenant_id).order_by(d.c.title))
    # NB: the `status` param below filters the latest VERSION's status; this one is the
    # document's own lifecycle. They are different axes and must not be conflated.
    if not include_archived:
        stmt = stmt.where(d.c.status != "ARCHIVED")
    if document_type:
        stmt = stmt.where(d.c.document_type == document_type)
    if q:
        stmt = stmt.where(func.lower(d.c.title).like(f"%{q.lower()}%"))
    # issue #13: same count-and-merge shape as evidence.py's _link_counts(). "Audits linked"
    # counts DISTINCT ASSESSMENTS, not distinct response rows — a document cited by three
    # points in the same audit is linked to that ONE audit, not three.
    cd = t("control_documents")
    control_counts = dict(conn.execute(
        select(cd.c.document_id, func.count())
        .where(cd.c.tenant_id == user.tenant_id)
        .group_by(cd.c.document_id)
    ).all())
    rd, resp = t("response_documents"), t("responses")
    audit_counts = dict(conn.execute(
        select(rd.c.document_id, func.count(func.distinct(resp.c.assessment_id)))
        .select_from(rd.join(resp, rd.c.response_id == resp.c.id))
        .where(rd.c.tenant_id == user.tenant_id)
        .group_by(rd.c.document_id)
    ).all())
    today = today_iso()
    out = []
    for r in conn.execute(stmt).mappings():
        pub = conn.execute(select(dv.c.version_label).where(
            dv.c.id == r["current_published_version_id"])).scalar() if r["current_published_version_id"] else None
        latest = conn.execute(
            select(dv.c.status, dv.c.version_label).where(dv.c.document_id == r["id"])
            .order_by(dv.c.major.desc(), dv.c.minor.desc()).limit(1)).mappings().first()
        item = {**dict(r),
                "published_version": pub,
                "latest_version": latest["version_label"] if latest else None,
                "latest_status": latest["status"] if latest else None,
                "review_status": review_status(r["next_review_at"], today),
                "linked_controls": control_counts.get(r["id"], 0),
                "linked_audits": audit_counts.get(r["id"], 0)}
        if status and item["latest_status"] != status:
            continue
        out.append(item)
    return out


@router.post("", status_code=201)
def create_document(body: DocumentIn, user: Principal = Depends(require("documents", "add"))):
    ppl = t("people")
    fmt = _check_format(body.content_format)
    _validate_sheet(body.content, fmt)
    # validated here so a bad type is a readable 400, not a 500 CheckViolation from Postgres
    if body.document_type not in vocabularies.DOCUMENT_TYPES:
        raise HTTPException(400, f"document_type must be one of: "
                                 f"{', '.join(vocabularies.DOCUMENT_TYPES)}")
    with engine.begin() as conn:
        if conn.execute(select(ppl.c.id).where(
                ppl.c.id == body.owner_person_id,
                ppl.c.tenant_id == user.tenant_id)).first() is None:
            raise HTTPException(400, "owner must be a person in this organisation")
        doc_id, ver_id, now = str(uuid.uuid4()), str(uuid.uuid4()), now_iso()
        conn.execute(insert(t("documents")).values(
            id=doc_id, tenant_id=user.tenant_id, title=body.title,
            document_type=body.document_type, classification=body.classification,
            write_mode="AUTHORED", owner_person_id=body.owner_person_id,
            description=body.description, review_cadence_months=body.review_cadence_months,
            status="ACTIVE", created_at=now, updated_at=now))
        # every document starts life as a v1.0 DRAFT to author into
        conn.execute(insert(t("document_versions")).values(
            id=ver_id, tenant_id=user.tenant_id, document_id=doc_id, major=1, minor=0,
            content=_clean(body.content, fmt) or "", content_format=fmt, status="DRAFT",
            created_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now, updated_at=now))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.created", entity_type="document", entity_id=doc_id,
                 detail={"title": body.title, "type": body.document_type})
    return {"id": doc_id, "version_id": ver_id}


@router.get("/{doc_id}")
def document_detail(doc_id: str, user: Principal = Depends(require("documents", "view")),
                    conn=Depends(get_conn)):
    doc = _doc(conn, user, doc_id)
    ppl = t("people")
    owner = conn.execute(select(ppl.c.id, ppl.c.full_name).where(
        ppl.c.id == doc["owner_person_id"])).mappings().first()
    versions = _versions(conn, doc_id)
    draft = next((v for v in versions if v["status"] in OPEN), None)
    approval = _latest_approval(conn, draft["id"]) if draft else None
    if draft:
        # The rich editor cannot be handed markdown: TipTap parses whatever string it is
        # given as HTML, so markdown source collapses into one paragraph of literal text
        # and saving that back would destroy the document. Convert here — server-side,
        # with the same md_to_html the PDF has always used — so the editor only ever sees
        # HTML and "save as HTML" is honest rather than a relabelling of markdown bytes.
        #
        # SHEET gets no `editor_html` at all: it isn't markup, it's JSON, and running it
        # through md_to_html would be nonsense the frontend never reads anyway —
        # SheetEditor parses `content` itself via `render.parse_sheet`'s JS-side equivalent.
        if draft["content_format"] == "SHEET":
            editor_html = None
        elif draft["content_format"] == "HTML":
            editor_html = draft["content"]
        else:
            editor_html = sanitize_document_html(render.md_to_html(draft["content"] or ""))
        draft = {**draft, "editor_html": editor_html}
    return {**doc, "owner": dict(owner) if owner else None,
            "review_status": review_status(doc["next_review_at"], today_iso()),
            "versions": versions, "open_version": draft, "approval": approval}


@router.get("/{doc_id}/links")
def document_links(doc_id: str, user: Principal = Depends(require("documents", "view")),
                   conn=Depends(get_conn)):
    """Reverse lookup for the "Linked Controls" / "Linked Audit Point" sections (issue #13):
    which controls and audit points cite this document directly. Mirrors library.py's
    control_detail() linked_documents query, in the opposite direction. Attach/detach for
    controls reuses the existing control-side routes (library.py); for audit points it
    reuses the response_documents routes in assessments.py — no new write endpoints here,
    this is read-only.
    """
    _doc(conn, user, doc_id)
    cd, controls = t("control_documents"), t("controls")
    linked_controls = [dict(r) for r in conn.execute(
        select(controls.c.id, controls.c.code, controls.c.statement)
        .join(cd, cd.c.control_id == controls.c.id)
        .where(cd.c.document_id == doc_id, cd.c.tenant_id == user.tenant_id)
        .order_by(controls.c.code)).mappings()]
    rd, resp, q, a = (t("response_documents"), t("responses"), t("questions"), t("assessments"))
    linked_audit_points = [dict(r) for r in conn.execute(
        select(a.c.id.label("assessment_id"), a.c.title.label("assessment_title"),
               a.c.bank_name, resp.c.question_id, q.c.number, q.c.text)
        .select_from(rd.join(resp, rd.c.response_id == resp.c.id)
                       .join(q, resp.c.question_id == q.c.id)
                       .join(a, resp.c.assessment_id == a.c.id))
        .where(rd.c.document_id == doc_id, rd.c.tenant_id == user.tenant_id)
        .order_by(a.c.title, q.c.number)).mappings()]
    return {"controls": linked_controls, "audit_points": linked_audit_points}


class DocumentPatch(StrictModel):
    title: str | None = None
    document_type: str | None = None
    classification: str | None = None
    owner_person_id: str | None = None
    description: str | None = None
    review_cadence_months: int | None = None
    next_review_at: IsoDate = None
    status: str | None = None           # ACTIVE | ARCHIVED — retire without deleting


@router.patch("/{doc_id}")
def update_document(doc_id: str, body: DocumentPatch,
                    user: Principal = Depends(require("documents", "edit"))):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    if "status" in vals and vals["status"] not in ("ACTIVE", "ARCHIVED"):
        raise HTTPException(400, "status must be ACTIVE or ARCHIVED")
    # issue #13: Type/Classification/Owner/Review were all set once at creation and never
    # editable again. classification/owner/review_cadence_months already accepted PATCHes;
    # document_type didn't, and needs the same 400-on-bad-value guard create_document uses.
    if "document_type" in vals and vals["document_type"] not in vocabularies.DOCUMENT_TYPES:
        raise HTTPException(400, f"document_type must be one of: "
                                 f"{', '.join(vocabularies.DOCUMENT_TYPES)}")
    vals["updated_at"] = now_iso()
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
        if "owner_person_id" in vals:
            # create_document validates this; update_document didn't, so a foreign
            # person_id reached the DB and raised an unhandled ForeignKeyViolation (500)
            # instead of the same 400 the create path gives.
            if conn.execute(select(t("people").c.id).where(
                    t("people").c.id == vals["owner_person_id"],
                    t("people").c.tenant_id == user.tenant_id)).first() is None:
                raise HTTPException(400, "owner must be a person in this organisation")
        conn.execute(update(t("documents")).where(
            t("documents").c.id == doc_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.updated", entity_type="document", entity_id=doc_id,
                 detail=vals)
    return {"ok": True}


@router.delete("/{doc_id}")
def delete_document(doc_id: str, user: Principal = Depends(require("documents", "delete"))):
    """Really delete a document — unlike Evidence's delete_evidence (which always succeeds
    and nulls out whatever pointed at it), a document can BE a Statement of Applicability's
    cited artifact, an access-review campaign's output, or the organisation's own NDA —
    compliance records in their own right, not incidental references. Refuse rather than
    silently corrupt one of those; same "block and name it" philosophy as delete_person.
    """
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
        blockers = _document_blockers(conn, user.tenant_id, doc_id)
        if blockers:
            raise HTTPException(409, "this document is still referenced by "
                                     + ", ".join(blockers)
                                     + " — remove those references first, or archive it instead")
        conn.execute(delete(t("documents")).where(
            t("documents").c.id == doc_id, t("documents").c.tenant_id == user.tenant_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.deleted", entity_type="document", entity_id=doc_id)
    return {"ok": True}


# ------------------------------------------------------------------ versions

class VersionEdit(StrictModel):
    content: str | None = None
    changelog: str | None = None
    content_format: str | None = None


@router.patch("/{doc_id}/versions/{version_id}")
def edit_version(doc_id: str, version_id: str, body: VersionEdit,
                 user: Principal = Depends(require("documents", "edit"))):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    dv = t("document_versions")
    with engine.begin() as conn:
        _require_active(_doc(conn, user, doc_id))
        v = conn.execute(select(dv).where(
            dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
        if v is None:
            raise HTTPException(404, "version not found")
        if v["status"] != "DRAFT":
            raise HTTPException(409, "only a DRAFT version can be edited")
        # Sanitise against the format this save will LEAVE the row in, and sanitise the
        # bytes that will actually be stored — which may be the EXISTING content.
        #
        # Doing this only `if "content" in vals` was a hole: markdown rows are deliberately
        # never sanitised, so a PATCH carrying content_format:"HTML" alone reclassified
        # never-cleaned bytes as HTML. DocBody then renders them through
        # dangerouslySetInnerHTML — including on the unauthenticated signing page.
        fmt = _check_format(vals.get("content_format", v["content_format"]))
        if fmt == "HTML":
            vals["content"] = _clean(vals.get("content", v["content"]), fmt)
        elif "content" in vals:
            vals["content"] = _clean(vals["content"], fmt)
        # Same "the row this save will LEAVE behind" reasoning as the sanitiser above:
        # validate whatever content will actually be stored, whether it arrived in this
        # PATCH or is the version's existing content and only the format changed.
        _validate_sheet(vals.get("content", v["content"]), fmt)
        vals["updated_at"] = now_iso()
        conn.execute(update(dv).where(dv.c.id == version_id).values(**vals))
    return {"ok": True}


class NewVersion(StrictModel):
    bump: str = "minor"        # minor | major


@router.post("/{doc_id}/versions", status_code=201)
def new_version(doc_id: str, body: NewVersion,
                user: Principal = Depends(require("documents", "add"))):
    if body.bump not in ("minor", "major"):
        raise HTTPException(400, "bump must be 'minor' or 'major'")
    dv = t("document_versions")
    with engine.begin() as conn:
        _require_active(_doc(conn, user, doc_id))
        versions = _versions(conn, doc_id)
        if any(v["status"] in OPEN for v in versions):
            raise HTTPException(409, "there is already an open draft — publish or discard it first")
        base = next((v for v in versions if v["status"] == "PUBLISHED"),
                    versions[0] if versions else None)
        if base is None:
            raise HTTPException(400, "document has no version to build on")
        major, minor = (base["major"] + 1, 0) if body.bump == "major" \
            else (base["major"], base["minor"] + 1)
        ver_id, now = str(uuid.uuid4()), now_iso()
        conn.execute(insert(dv).values(
            id=ver_id, tenant_id=user.tenant_id, document_id=doc_id,
            major=major, minor=minor,
            # carry the base's format forward — re-sanitising here would rewrite the bytes
            # of a row whose hash a signature may already pin
            content=base["content"], content_format=base["content_format"], status="DRAFT",
            created_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now, updated_at=now))
    return {"version_id": ver_id, "version_label": f"{major}.{minor}"}


@router.delete("/{doc_id}/versions/{version_id}", status_code=204)
def discard_draft(doc_id: str, version_id: str,
                  user: Principal = Depends(require("documents", "edit"))):
    """Throw away an unwanted draft, returning the document to its published version.

    Gated on documents.edit, not .delete: `document_versions.status` has no DISCARDED
    member, and an Editor (who holds every action but delete) must be able to abandon
    their own draft.
    """
    dv = t("document_versions")
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
        v = conn.execute(select(dv).where(
            dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
        if v is None:
            raise HTTPException(404, "version not found")
        if v["status"] != "DRAFT":
            raise HTTPException(409, "only a DRAFT version can be discarded")
        if len(_versions(conn, doc_id)) == 1:
            raise HTTPException(409, "a document must keep at least one version")
        # document_approvals -> document_approval_decisions both CASCADE from
        # document_versions. decide() already logs each individual decision as it happens,
        # but that only captures ONE approver's state+comment per event — the full round
        # (every approver's state, whether they ever decided) would otherwise vanish
        # with no trace once this DELETE fires. Snapshot it into the append-only
        # activity_log first, which the CASCADE never touches.
        ap, dec, ppl = t("document_approvals"), t("document_approval_decisions"), t("people")
        rounds = conn.execute(select(ap.c.id, ap.c.status, ap.c.threshold_required).where(
            ap.c.document_version_id == version_id)).mappings().all()
        history = []
        for r in rounds:
            decisions = conn.execute(
                select(dec.c.state, dec.c.comment, ppl.c.full_name)
                .join(ppl, dec.c.approver_person_id == ppl.c.id)
                .where(dec.c.approval_id == r["id"])).mappings().all()
            history.append({"round_status": r["status"], "threshold": r["threshold_required"],
                            "decisions": [dict(d) for d in decisions]})
        conn.execute(delete(dv).where(dv.c.id == version_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.draft_discarded", entity_type="document", entity_id=doc_id,
                 detail={"version": v["version_label"], "approval_history": history})
    return Response(status_code=204)


# ------------------------------------------------------------------ approval (M-of-N)

class SubmitIn(StrictModel):
    threshold_required: int
    approver_person_ids: list[str]


@router.post("/{doc_id}/versions/{version_id}/submit", status_code=201)
def submit_for_approval(doc_id: str, version_id: str, body: SubmitIn,
                        user: Principal = Depends(require("documents", "edit"))):
    approvers = list(dict.fromkeys(body.approver_person_ids))   # de-dup, keep order
    if not approvers:
        raise HTTPException(400, "pick at least one approver")
    if body.threshold_required < 1:
        raise HTTPException(400, "threshold must be at least 1")
    if body.threshold_required > len(approvers):
        raise HTTPException(400,
                            f"threshold {body.threshold_required} exceeds the "
                            f"{len(approvers)} approver(s) picked")
    dv, ppl = t("document_versions"), t("people")
    with engine.begin() as conn:
        _require_active(_doc(conn, user, doc_id))
        v = conn.execute(select(dv).where(
            dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
        if v is None:
            raise HTTPException(404, "version not found")
        if v["status"] != "DRAFT":
            raise HTTPException(409, "only a DRAFT can be submitted for approval")
        valid = set(conn.execute(select(ppl.c.id).where(
            ppl.c.tenant_id == user.tenant_id, ppl.c.id.in_(approvers))).scalars())
        missing = [a for a in approvers if a not in valid]
        if missing:
            raise HTTPException(400, "some approvers are not people in this organisation")
        aid, now = str(uuid.uuid4()), now_iso()
        # Void any prior round on this version (e.g. one that was REJECTED and sent the
        # version back to DRAFT). Otherwise a stale round lingers as a candidate for "the
        # latest round" and — on a same-second opened_at tie — could shadow this one and
        # permanently block a legitimately-approved publish. One non-cancelled round, always.
        conn.execute(update(t("document_approvals")).where(
            t("document_approvals").c.document_version_id == version_id,
            t("document_approvals").c.status != "CANCELLED").values(
            status="CANCELLED", closed_at=now))
        conn.execute(insert(t("document_approvals")).values(
            id=aid, tenant_id=user.tenant_id, document_version_id=version_id,
            threshold_required=body.threshold_required, status="PENDING", opened_at=now))
        for pid in approvers:
            conn.execute(insert(t("document_approval_decisions")).values(
                id=str(uuid.uuid4()), tenant_id=user.tenant_id, approval_id=aid,
                approver_person_id=pid, state="PENDING", created_at=now))
        conn.execute(update(dv).where(dv.c.id == version_id).values(
            status="PENDING_APPROVAL"))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.submitted", entity_type="document_version",
                 entity_id=version_id,
                 detail={"threshold": body.threshold_required, "approvers": len(approvers)})
    return {"approval_id": aid, "threshold": body.threshold_required,
            "approvers": len(approvers)}


class DecideIn(StrictModel):
    approver_person_id: str
    state: str                 # APPROVED | REJECTED | ABSTAINED
    comment: str | None = None


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: DecideIn,
           user: Principal = Depends(require("documents", "approve"))):
    if body.state not in ("APPROVED", "REJECTED", "ABSTAINED"):
        raise HTTPException(400, "state must be APPROVED, REJECTED or ABSTAINED")
    ap, dec, dv = (t("document_approvals"), t("document_approval_decisions"),
                   t("document_versions"))
    with engine.begin() as conn:
        a = conn.execute(select(ap).where(
            ap.c.id == approval_id, ap.c.tenant_id == user.tenant_id)).mappings().first()
        if a is None:
            raise HTTPException(404, "approval round not found")
        if a["status"] != "PENDING":
            raise HTTPException(409, f"this round is already {a['status'].lower()}")
        d = conn.execute(select(dec).where(
            dec.c.approval_id == approval_id,
            dec.c.approver_person_id == body.approver_person_id)).mappings().first()
        if d is None:
            raise HTTPException(404, "that person is not an approver on this round")
        now = now_iso()
        conn.execute(update(dec).where(dec.c.id == d["id"]).values(
            state=body.state, comment=body.comment, decided_at=now))

        # recompute the round
        rows = list(conn.execute(select(dec.c.state).where(
            dec.c.approval_id == approval_id)).scalars())
        approved = sum(1 for s in rows if s == "APPROVED")
        if body.state == "REJECTED":
            conn.execute(update(ap).where(ap.c.id == approval_id).values(
                status="REJECTED", closed_at=now))
            conn.execute(update(dv).where(dv.c.id == a["document_version_id"]).values(
                status="DRAFT"))                       # back to editing
            new_status = "REJECTED"
        elif approved >= a["threshold_required"]:
            conn.execute(update(ap).where(ap.c.id == approval_id).values(
                status="APPROVED", closed_at=now))
            new_status = "APPROVED"
        else:
            new_status = "PENDING"
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.decided", entity_type="document_approval",
                 entity_id=approval_id,
                 # the comment is the reason a rejection happened, and it must survive even
                 # if the draft is later discarded — document_approvals and its decisions
                 # CASCADE from document_versions, but activity_log is append-only and never
                 # touched by that delete, so this is where the rejection reason actually
                 # lives once a discarded draft's own row is gone.
                 detail={"by": body.approver_person_id, "state": body.state,
                         "comment": body.comment})
    return {"round_status": new_status, "approved": approved,
            "threshold": a["threshold_required"],
            "can_publish": new_status == "APPROVED"}


@router.post("/{doc_id}/versions/{version_id}/publish")
def publish(doc_id: str, version_id: str, user: Principal = Depends(require("documents", "publish"))):
    dv = t("document_versions")
    with engine.begin() as conn:
        doc = _require_active(_doc(conn, user, doc_id))
        # Lock the version row: two concurrent publishes must serialise, or both would
        # pass the guard, render two PDFs (one orphaned) and double the audit entry.
        # The loser wakes on the committed row, sees PUBLISHED, and 409s cleanly.
        v = conn.execute(select(dv).where(
            dv.c.id == version_id, dv.c.document_id == doc_id).with_for_update()).mappings().first()
        if v is None:
            raise HTTPException(404, "version not found")
        if v["status"] == "PUBLISHED":
            raise HTTPException(409, "already published")
        if v["status"] != "PENDING_APPROVAL":
            raise HTTPException(409, "submit the version for approval before publishing")
        appr = _latest_approval(conn, version_id)
        if not appr or appr["status"] != "APPROVED":
            need = appr["threshold_required"] if appr else "?"
            got = appr["approved"] if appr else 0
            raise HTTPException(409,
                                f"not enough approvals to publish ({got} of {need})")
        now = now_iso()
        file_id = _render_and_store(conn, user, doc, dict(v))
        # supersede whatever was published before (content unchanged → freeze allows)
        if doc["current_published_version_id"]:
            old_vid = doc["current_published_version_id"]
            conn.execute(update(dv).where(dv.c.id == old_vid).values(status="SUPERSEDED"))
            # kill any still-live attestation links for the superseded version — nobody should
            # sign outdated content; a fresh campaign will re-request against the new version.
            ds, st = t("document_signatures"), t("signing_tokens")
            conn.execute(update(st).where(
                st.c.consumed_at.is_(None), st.c.revoked_at.is_(None),
                st.c.document_signature_id.in_(
                    select(ds.c.id).where(ds.c.document_version_id == old_vid))
            ).values(revoked_at=now))
        conn.execute(update(dv).where(dv.c.id == version_id).values(
            status="PUBLISHED", published_at=now, file_id=file_id))
        upd = {"current_published_version_id": version_id}
        if doc["review_cadence_months"]:
            upd["next_review_at"] = add_months(today_iso(), doc["review_cadence_months"])
        conn.execute(update(t("documents")).where(
            t("documents").c.id == doc_id).values(**upd))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.published", entity_type="document_version",
                 entity_id=version_id, detail={"version": v["version_label"]})
    return {"published": True, "version_label": v["version_label"], "file_id": file_id}


# ------------------------------------------------------------------ render + diff

@router.get("/{doc_id}/versions/{version_id}/render.pdf")
def render_version(doc_id: str, version_id: str,
                   disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
                   user: Principal = Depends(require("documents", "view")), conn=Depends(get_conn)):
    doc = _doc(conn, user, doc_id)
    dv, files = t("document_versions"), t("files")
    v = conn.execute(select(dv).where(
        dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
    if v is None:
        raise HTTPException(404, "version not found")
    # A controlled document should arrive named, not as "render.pdf" — the title and
    # version are what someone files it under.
    headers = {"Content-Disposition": _disposition(
        disposition, f'{doc["title"][:80]} v{v["version_label"]}.pdf')}
    if v["file_id"]:                                    # published → serve the stored PDF
        key = conn.execute(select(files.c.storage_key).where(
            files.c.id == v["file_id"])).scalar()
        path = storage.path_for(key)
        if path.exists():
            data = path.read_bytes()
            return Response(data, media_type="application/pdf", headers=headers)
    # draft preview → render on the fly
    brand = branding.letterhead(conn, user.tenant_id)
    pdf, _ = render.render_pdf(title=doc["title"], body_md=v["content"] or "",
                               classification=doc["classification"],
                               version_label=v["version_label"], status=v["status"],
                               content_format=v["content_format"],
                               org=brand["org"], logo_data_uri=brand["logo_data_uri"],
                               logo_w=brand["width"], logo_h=brand["height"],
                               image_resolver=image_resolver(conn, user.tenant_id))
    return Response(pdf, media_type="application/pdf", headers=headers)


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{doc_id}/versions/{version_id}/render.docx")
def render_version_docx(doc_id: str, version_id: str,
                        disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
                        user: Principal = Depends(require("documents", "view")),
                        conn=Depends(get_conn)):
    """Word export. Always rendered on the fly — only the PDF is the controlled artefact
    stored against the version, and `document_versions` has just the one `file_id`."""
    doc = _doc(conn, user, doc_id)
    dv = t("document_versions")
    v = conn.execute(select(dv).where(
        dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
    if v is None:
        raise HTTPException(404, "version not found")
    brand = branding.letterhead(conn, user.tenant_id)
    data = docx_export.render_docx(
        title=doc["title"], body_html=v["content"] or "",
        classification=doc["classification"], version_label=v["version_label"],
        status=v["status"], content_format=v["content_format"],
        org=brand["org"], logo=brand["logo"], logo_mime=brand["logo_mime"],
        image_resolver=image_resolver(conn, user.tenant_id))
    return Response(data, media_type=DOCX_MIME, headers={
        "Content-Disposition": _disposition(
            disposition, f'{doc["title"][:80]} v{v["version_label"]}.docx')})


@router.get("/{doc_id}/versions/{version_id}/render.xlsx")
def render_version_xlsx(doc_id: str, version_id: str,
                        disposition: str = Query("attachment", pattern="^(attachment|inline)$"),
                        user: Principal = Depends(require("documents", "view")),
                        conn=Depends(get_conn)):
    """Spreadsheet export, for SHEET versions only (P5-S2b).

    Rendered on the fly like the DOCX — only the PDF is the controlled artefact stored
    against the version. Offered only for SHEET because there is no sensible workbook
    projection of a prose policy; asking for one on a MARKDOWN/HTML version is a 400 rather
    than a confusing single-cell file.
    """
    doc = _doc(conn, user, doc_id)
    dv = t("document_versions")
    v = conn.execute(select(dv).where(
        dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
    if v is None:
        raise HTTPException(404, "version not found")
    if v["content_format"] != "SHEET":
        raise HTTPException(400, "only spreadsheet documents can be exported as .xlsx")
    try:
        data = xlsx_io.build_sheet_workbook(doc["title"], v["content"] or "")
    except render.SheetFormatError as e:
        raise HTTPException(400, f"invalid spreadsheet content: {e}") from e
    return Response(data, media_type=XLSX_MIME, headers={
        "Content-Disposition": _disposition(
            disposition, f'{doc["title"][:80]} v{v["version_label"]}.xlsx')})


def _diff_lines(content: str | None, fmt: str) -> list[str]:
    """One comparable line per block.

    TipTap serialises a whole document onto a single line, so a raw splitlines() diff of
    HTML always reports exactly 1 added and 1 removed no matter what changed — a diff that
    can never be wrong and is therefore useless. Project HTML to block-level text instead.

    Known limitation, not fixed here: this is a TEXT diff. A change to markup alone with the
    same visible text — a link's href, a heading level, cell colspan — reads as "no change".
    A correct diff would need to compare serialized HTML per block, not just its rendered
    text; that is a larger redesign than this projection, and out of scope for now.
    """
    if fmt == "SHEET":
        # A workbook is one long line of JSON, so a raw splitlines() diff reports "1 added,
        # 1 removed" for every possible edit. Project to one line per non-empty cell.
        try:
            return render.sheet_diff_lines(content or "")
        except render.SheetFormatError:
            # A draft can hold content the validator rejects only if it was written before a
            # format change; a broken diff must not 500 the endpoint.
            return (content or "").splitlines()
    if fmt != "HTML":
        return (content or "").splitlines()
    from lxml import html as lhtml  # noqa: PLC0415 — only needed on the HTML branch
    root = lhtml.fragment_fromstring(content or "", create_parent="div")
    # Iterating `for el in root` yields only child ELEMENTS. Bare text at the top level
    # (root.text) and text trailing a block (el.tail) were dropped entirely, so a version
    # whose body was not wrapped in block tags diffed as "no change at all".
    #
    # el.text_content() also concatenates a list's items with no separator — "Lock
    # screens" + "Rotate keys" read as one word, "Lock screensRotate keys" — so each
    # element's own text nodes are joined with itertext() instead, one space apart.
    parts = [root.text or ""]
    for el in root:
        parts.append(" ".join(el.itertext()))
        parts.append(el.tail or "")
    return [line for p in parts if (line := " ".join(p.split()))]


@router.get("/{doc_id}/diff")
def diff_versions(doc_id: str, from_version: str = Query(...), to_version: str = Query(...),
                  user: Principal = Depends(require("documents", "view")), conn=Depends(get_conn)):
    _doc(conn, user, doc_id)
    dv = t("document_versions")
    def load(vid):
        r = conn.execute(select(dv.c.content, dv.c.version_label, dv.c.content_format).where(
            dv.c.id == vid, dv.c.document_id == doc_id)).mappings().first()
        if r is None:
            raise HTTPException(404, "version not found")
        return r
    a, b = load(from_version), load(to_version)
    diff = list(difflib.unified_diff(
        _diff_lines(a["content"], a["content_format"]),
        _diff_lines(b["content"], b["content_format"]),
        fromfile=f"v{a['version_label']}", tofile=f"v{b['version_label']}", lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {"from": a["version_label"], "to": b["version_label"],
            "added": added, "removed": removed, "diff": diff}


# ------------------------------------------------------------------ attestation (M9b · D-SIGN)
# Audiences say WHO must sign; a campaign fans out a magic link per person; coverage %
# is computed LIVE from the audience (never stored), so a new hire lowers it immediately.

def _audience_targets(conn, tenant_id: str, doc_id: str) -> list[dict]:
    """Active people the document's audience rules currently resolve to (live from people).
    Mirrors v_document_expected_signers' matching, but without needing a published version —
    so it doubles as the audience-builder preview and the campaign target list."""
    q = text("""
      SELECT DISTINCT p.id, p.full_name, p.department, p.email
        FROM document_audiences a
        JOIN v_people_effective_state p ON p.tenant_id = a.tenant_id
         AND p.effective_state = 'ACTIVE'
         AND ( a.rule = 'ALL_EMPLOYEES'
            OR (a.rule = 'DEPARTMENT' AND p.department = a.value)
            OR (a.rule = 'EXPLICIT'   AND p.id = a.person_id) )
       WHERE a.document_id = :doc AND a.tenant_id = :tid
       ORDER BY p.full_name""")
    return [dict(r) for r in conn.execute(q, {"doc": doc_id, "tid": tenant_id}).mappings()]


def _default_consent(title: str, version_label: str) -> str:
    return (f"I confirm that I have read and understood “{title}” "
            f"(version {version_label}) and I agree to comply with it.")


class AudienceRule(StrictModel):
    rule: str                       # ALL_EMPLOYEES | DEPARTMENT | EXPLICIT
    value: str | None = None        # department name, when rule=DEPARTMENT
    person_id: str | None = None    # the person, when rule=EXPLICIT


class AudienceIn(StrictModel):
    rules: list[AudienceRule]


@router.get("/{doc_id}/audiences")
def list_audiences(doc_id: str, user: Principal = Depends(require("documents", "view")),
                   conn=Depends(get_conn)):
    _doc(conn, user, doc_id)
    da = t("document_audiences")
    rules = [dict(r) for r in conn.execute(
        select(da).where(da.c.document_id == doc_id).order_by(da.c.created_at)).mappings()]
    return {"rules": rules, "targeted": len(_audience_targets(conn, user.tenant_id, doc_id))}


@router.post("/{doc_id}/audiences")
def set_audiences(doc_id: str, body: AudienceIn,
                  user: Principal = Depends(require("documents", "edit"))):
    # normalise + validate each rule's shape (mirrors the da_rule_shape CHECK, with a
    # friendly 400 instead of a raw constraint error)
    clean: list[tuple] = []
    explicit_ids: set[str] = set()
    for r in body.rules:
        if r.rule == "ALL_EMPLOYEES":
            clean.append(("ALL_EMPLOYEES", None, None))
        elif r.rule == "DEPARTMENT":
            if not (r.value or "").strip():
                raise HTTPException(400, "a DEPARTMENT rule needs a department name")
            clean.append(("DEPARTMENT", r.value.strip(), None))
        elif r.rule == "EXPLICIT":
            if not r.person_id:
                raise HTTPException(400, "an EXPLICIT rule needs a person")
            explicit_ids.add(r.person_id)
            clean.append(("EXPLICIT", None, r.person_id))
        else:
            raise HTTPException(400, f"unknown audience rule {r.rule!r}")
    clean = list(dict.fromkeys(clean))          # drop exact duplicates
    with engine.begin() as conn:
        _require_active(_doc(conn, user, doc_id))
        if explicit_ids:                        # every EXPLICIT person must be in this tenant
            found = set(conn.execute(select(t("people").c.id).where(
                t("people").c.tenant_id == user.tenant_id,
                t("people").c.id.in_(explicit_ids))).scalars())
            if explicit_ids - found:
                raise HTTPException(400, "some people are not in this organisation")
        da = t("document_audiences")
        conn.execute(delete(da).where(da.c.document_id == doc_id))
        now = now_iso()
        for rule, value, pid in clean:
            conn.execute(insert(da).values(
                id=str(uuid.uuid4()), tenant_id=user.tenant_id, document_id=doc_id,
                rule=rule, value=value, person_id=pid, created_at=now))
        targeted = len(_audience_targets(conn, user.tenant_id, doc_id))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.audience_set", entity_type="document", entity_id=doc_id,
                 detail={"rules": len(clean), "targeted": targeted})
    return {"ok": True, "targeted": targeted}


class CampaignIn(StrictModel):
    due_days: int = 30
    expires_days: int = 30
    consent_text: str | None = None


@router.post("/{doc_id}/attestation-campaign", status_code=201)
def start_attestation_campaign(doc_id: str, body: CampaignIn,
                               user: Principal = Depends(require("documents", "edit"))):
    ds, st, dv = t("document_signatures"), t("signing_tokens"), t("document_versions")
    with engine.begin() as conn:
        doc = _require_active(_doc(conn, user, doc_id))
        vid = doc["current_published_version_id"]
        if not vid:
            raise HTTPException(400, "publish a version before requesting attestations")
        ver = conn.execute(select(dv).where(dv.c.id == vid)).mappings().first()
        targets = _audience_targets(conn, user.tenant_id, doc_id)
        if not targets:
            raise HTTPException(400, "set an audience with at least one active person first")
        consent = (body.consent_text or "").strip() or _default_consent(
            doc["title"], ver["version_label"])
        now, due, expires = now_iso(), now_plus_days(body.due_days), now_plus_days(body.expires_days)
        issued = []
        for p in targets:
            sig = conn.execute(select(ds).where(
                ds.c.document_version_id == vid, ds.c.person_id == p["id"])).mappings().first()
            if sig and sig["state"] == "SIGNED":
                continue                        # already attested this exact version
            if sig is None:
                sig_id = str(uuid.uuid4())
                conn.execute(insert(ds).values(
                    id=sig_id, tenant_id=user.tenant_id, document_version_id=vid,
                    person_id=p["id"], state="REQUESTED", requested_at=now, due_at=due))
            else:                               # reissue: one live link per person
                sig_id = sig["id"]
                conn.execute(update(ds).where(ds.c.id == sig_id).values(due_at=due))
                conn.execute(update(st).where(
                    st.c.document_signature_id == sig_id,
                    st.c.consumed_at.is_(None), st.c.revoked_at.is_(None)).values(revoked_at=now))
            raw = secrets.token_urlsafe(32)     # 256-bit; the raw value lives ONLY in the link
            conn.execute(insert(st).values(
                id=str(uuid.uuid4()), tenant_id=user.tenant_id,
                token_hash=hashlib.sha256(raw.encode()).hexdigest(), purpose="ATTEST",
                document_signature_id=sig_id, sent_to_email=p["email"], consent_text=consent,
                issued_at=now, expires_at=expires))
            issued.append({"person_id": p["id"], "full_name": p["full_name"],
                           "email": p["email"], "token": raw, "sign_path": f"/sign/{raw}"})
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.attestation_campaign", entity_type="document_version",
                 entity_id=vid, detail={"issued": len(issued)})
    return {"version_label": ver["version_label"], "issued": issued,
            "already_signed": len(targets) - len(issued)}


@router.get("/{doc_id}/coverage")
def coverage(doc_id: str, user: Principal = Depends(require("documents", "view")), conn=Depends(get_conn)):
    doc = _doc(conn, user, doc_id)
    vid = doc["current_published_version_id"]
    if not vid:
        return {"published": False, "version_label": None, "expected": 0, "signed": 0,
                "outstanding": 0, "coverage_pct": None, "people": []}
    cov = conn.execute(text(
        "SELECT version_label, expected, signed, outstanding, coverage_pct "
        "FROM v_document_coverage WHERE version_id = :v"), {"v": vid}).mappings().first()
    people = [dict(r) for r in conn.execute(text("""
        SELECT x.person_id, p.full_name, p.department, p.email,
               coalesce(s.state, 'OUTSTANDING') AS state, s.signed_at, s.due_at
          FROM v_document_expected_signers x
          JOIN people p ON p.id = x.person_id
          LEFT JOIN document_signatures s
                 ON s.document_version_id = x.document_version_id AND s.person_id = x.person_id
         WHERE x.document_version_id = :v
         ORDER BY (s.state = 'SIGNED'), p.full_name"""), {"v": vid}).mappings()]
    dv = t("document_versions")
    ver_label = conn.execute(select(dv.c.version_label).where(dv.c.id == vid)).scalar()
    return {"published": True,
            "version_label": cov["version_label"] if cov else ver_label,
            "expected": cov["expected"] if cov else 0,
            "signed": cov["signed"] if cov else 0,
            "outstanding": cov["outstanding"] if cov else 0,
            "coverage_pct": float(cov["coverage_pct"]) if cov and cov["coverage_pct"] is not None else None,
            "people": people}
