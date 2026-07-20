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
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, insert, select, text, update

from api import activity, render, storage
from api.auth import Principal, get_current_user
from api.database import engine, get_conn, t
from api.util import IsoDate, add_months, now_iso, review_status, today_iso

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


def _render_and_store(conn, user: Principal, doc: dict, ver: dict) -> str:
    """Render the version to PDF, save it to the vault, return the new file_id."""
    pdf, _engine = render.render_pdf(
        title=doc["title"], body_md=ver["content"] or "",
        classification=doc["classification"], version_label=ver["version_label"],
        status="PUBLISHED")
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

class DocumentIn(BaseModel):
    title: str
    document_type: str = "POLICY"
    classification: str = "INTERNAL"
    owner_person_id: str
    description: str | None = None
    review_cadence_months: int | None = 12
    content: str | None = None          # optional starting markdown for v1.0


@router.get("")
def list_documents(document_type: str | None = Query(None), status: str | None = Query(None),
                   q: str | None = Query(None),
                   user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    d, ppl, dv = t("documents"), t("people"), t("document_versions")
    stmt = (select(d, ppl.c.full_name.label("owner_name"))
            .join(ppl, d.c.owner_person_id == ppl.c.id)
            .where(d.c.tenant_id == user.tenant_id).order_by(d.c.title))
    if document_type:
        stmt = stmt.where(d.c.document_type == document_type)
    if q:
        stmt = stmt.where(func.lower(d.c.title).like(f"%{q.lower()}%"))
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
                "review_status": review_status(r["next_review_at"], today)}
        if status and item["latest_status"] != status:
            continue
        out.append(item)
    return out


@router.post("", status_code=201)
def create_document(body: DocumentIn, user: Principal = Depends(get_current_user)):
    ppl = t("people")
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
            content=body.content or "", status="DRAFT",
            created_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now, updated_at=now))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.created", entity_type="document", entity_id=doc_id,
                 detail={"title": body.title, "type": body.document_type})
    return {"id": doc_id, "version_id": ver_id}


@router.get("/{doc_id}")
def document_detail(doc_id: str, user: Principal = Depends(get_current_user),
                    conn=Depends(get_conn)):
    doc = _doc(conn, user, doc_id)
    ppl = t("people")
    owner = conn.execute(select(ppl.c.id, ppl.c.full_name).where(
        ppl.c.id == doc["owner_person_id"])).mappings().first()
    versions = _versions(conn, doc_id)
    draft = next((v for v in versions if v["status"] in OPEN), None)
    approval = _latest_approval(conn, draft["id"]) if draft else None
    return {**doc, "owner": dict(owner) if owner else None,
            "review_status": review_status(doc["next_review_at"], today_iso()),
            "versions": versions, "open_version": draft, "approval": approval}


class DocumentPatch(BaseModel):
    title: str | None = None
    classification: str | None = None
    owner_person_id: str | None = None
    description: str | None = None
    review_cadence_months: int | None = None


@router.patch("/{doc_id}")
def update_document(doc_id: str, body: DocumentPatch,
                    user: Principal = Depends(get_current_user)):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
        conn.execute(update(t("documents")).where(
            t("documents").c.id == doc_id).values(**vals))
    activity.log(tenant_id=user.tenant_id, actor_user_id=user.user_id,
                 action="document.updated", entity_type="document", entity_id=doc_id,
                 detail=vals)
    return {"ok": True}


# ------------------------------------------------------------------ versions

class VersionEdit(BaseModel):
    content: str | None = None
    changelog: str | None = None


@router.patch("/{doc_id}/versions/{version_id}")
def edit_version(doc_id: str, version_id: str, body: VersionEdit,
                 user: Principal = Depends(get_current_user)):
    vals = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not vals:
        raise HTTPException(400, "nothing to update")
    dv = t("document_versions")
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
        v = conn.execute(select(dv).where(
            dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
        if v is None:
            raise HTTPException(404, "version not found")
        if v["status"] != "DRAFT":
            raise HTTPException(409, "only a DRAFT version can be edited")
        conn.execute(update(dv).where(dv.c.id == version_id).values(**vals))
    return {"ok": True}


class NewVersion(BaseModel):
    bump: str = "minor"        # minor | major


@router.post("/{doc_id}/versions", status_code=201)
def new_version(doc_id: str, body: NewVersion,
                user: Principal = Depends(get_current_user)):
    if body.bump not in ("minor", "major"):
        raise HTTPException(400, "bump must be 'minor' or 'major'")
    dv = t("document_versions")
    with engine.begin() as conn:
        _doc(conn, user, doc_id)
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
            major=major, minor=minor, content=base["content"], status="DRAFT",
            created_by_member_id=_member_id(conn, user.tenant_id, user.user_id),
            created_at=now, updated_at=now))
    return {"version_id": ver_id, "version_label": f"{major}.{minor}"}


# ------------------------------------------------------------------ approval (M-of-N)

class SubmitIn(BaseModel):
    threshold_required: int
    approver_person_ids: list[str]


@router.post("/{doc_id}/versions/{version_id}/submit", status_code=201)
def submit_for_approval(doc_id: str, version_id: str, body: SubmitIn,
                        user: Principal = Depends(get_current_user)):
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
        _doc(conn, user, doc_id)
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


class DecideIn(BaseModel):
    approver_person_id: str
    state: str                 # APPROVED | REJECTED | ABSTAINED
    comment: str | None = None


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: DecideIn,
           user: Principal = Depends(get_current_user)):
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
                 detail={"by": body.approver_person_id, "state": body.state})
    return {"round_status": new_status, "approved": approved,
            "threshold": a["threshold_required"],
            "can_publish": new_status == "APPROVED"}


@router.post("/{doc_id}/versions/{version_id}/publish")
def publish(doc_id: str, version_id: str, user: Principal = Depends(get_current_user)):
    dv = t("document_versions")
    with engine.begin() as conn:
        doc = _doc(conn, user, doc_id)
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
            conn.execute(update(dv).where(
                dv.c.id == doc["current_published_version_id"]).values(status="SUPERSEDED"))
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
                   user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    doc = _doc(conn, user, doc_id)
    dv, files = t("document_versions"), t("files")
    v = conn.execute(select(dv).where(
        dv.c.id == version_id, dv.c.document_id == doc_id)).mappings().first()
    if v is None:
        raise HTTPException(404, "version not found")
    if v["file_id"]:                                    # published → serve the stored PDF
        key = conn.execute(select(files.c.storage_key).where(
            files.c.id == v["file_id"])).scalar()
        path = storage.path_for(key)
        if path.exists():
            data = path.read_bytes()
            return Response(data, media_type="application/pdf")
    # draft preview → render on the fly
    pdf, _ = render.render_pdf(title=doc["title"], body_md=v["content"] or "",
                               classification=doc["classification"],
                               version_label=v["version_label"], status=v["status"])
    return Response(pdf, media_type="application/pdf")


@router.get("/{doc_id}/diff")
def diff_versions(doc_id: str, from_version: str = Query(...), to_version: str = Query(...),
                  user: Principal = Depends(get_current_user), conn=Depends(get_conn)):
    _doc(conn, user, doc_id)
    dv = t("document_versions")
    def load(vid):
        r = conn.execute(select(dv.c.content, dv.c.version_label).where(
            dv.c.id == vid, dv.c.document_id == doc_id)).mappings().first()
        if r is None:
            raise HTTPException(404, "version not found")
        return r
    a, b = load(from_version), load(to_version)
    diff = list(difflib.unified_diff(
        (a["content"] or "").splitlines(), (b["content"] or "").splitlines(),
        fromfile=f"v{a['version_label']}", tofile=f"v{b['version_label']}", lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {"from": a["version_label"], "to": b["version_label"],
            "added": added, "removed": removed, "diff": diff}
