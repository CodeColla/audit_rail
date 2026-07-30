"""Public, unauthenticated signing — the magic link (Sprint 3 / M9b · D-SIGN).

No session. The 256-bit token in the URL *is* the credential; we store only
sha256(token), so the raw value exists nowhere in the DB — solely in the link.

Everything (tenant, document, signer) is derived from the token row, and no
client-supplied id is ever trusted, so a token can only act within its own
tenant. RLS is inert today (the app is the table owner; policies aren't FORCEd —
see db/schema.sql), so this derive-from-the-token discipline is the belt that
holds tenant isolation without a GUC. When M15 lands app_rw + FORCE, this route
is the one that must also set audit_rail.tenant_id from tok["tenant_id"].
"""

from __future__ import annotations

import hashlib
import ipaddress
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import insert, select, text, update

from api.database import engine, t
from api.util import StrictModel, now_iso

router = APIRouter(prefix="/sign", tags=["signing"])

_GONE = {
    "consumed": "This link has already been used.",
    "revoked": "This link has been revoked.",
    "expired": "This link has expired.",
}


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _token_state(tok) -> str:
    """LIVE, or why not. ISO-8601 UTC strings compare lexicographically == chronologically."""
    if tok["revoked_at"]:
        return "revoked"
    if tok["consumed_at"]:
        return "consumed"
    if tok["expires_at"] <= now_iso():
        return "expired"
    return "LIVE"


def _client_ip(request: Request) -> str | None:
    """Best-effort signer IP for the evidence chain. Returns None (not a 500) for a
    non-IP host such as Starlette's TestClient 'testclient', so the inet insert is safe."""
    xff = request.headers.get("x-forwarded-for")
    cand = xff.split(",")[0].strip() if xff else (request.client.host if request.client else None)
    try:
        return str(ipaddress.ip_address(cand)) if cand else None
    except ValueError:
        return None


def _load_attestation(conn, tok):
    """The signature row + its version, document and person, from an ATTEST token."""
    ds, dv, d, ppl = (t("document_signatures"), t("document_versions"),
                      t("documents"), t("people"))
    sig = conn.execute(select(ds).where(ds.c.id == tok["document_signature_id"])).mappings().first()
    ver = conn.execute(select(dv).where(dv.c.id == sig["document_version_id"])).mappings().first()
    doc = conn.execute(select(d).where(d.c.id == ver["document_id"])).mappings().first()
    person = conn.execute(select(ppl).where(ppl.c.id == sig["person_id"])).mappings().first()
    return sig, ver, doc, person


@router.get("/{token}")
def sign_page(token: str):
    """What the signing page renders: the document, the consent wording, who's signing.
    Read-only — viewing never consumes the token."""
    st = t("signing_tokens")
    with engine.connect() as conn:
        tok = conn.execute(select(st).where(st.c.token_hash == _hash(token))).mappings().first()
        if tok is None:
            raise HTTPException(404, "This signing link is not valid.")
        state = _token_state(tok)
        if state != "LIVE":
            raise HTTPException(410, _GONE.get(state, "This link is no longer valid."))
        if tok["purpose"] != "ATTEST":
            raise HTTPException(400, "This link is not an attestation link.")
        _sig, ver, doc, person = _load_attestation(conn, tok)
        if ver["status"] != "PUBLISHED":            # a newer version has since superseded this one
            raise HTTPException(410, "This version has been superseded — ask for a fresh signing link.")
    return {"kind": "ATTEST", "document_title": doc["title"],
            "classification": doc["classification"], "version_label": ver["version_label"],
            # the public page is the second place content is rendered; without the format
            # an HTML version would show its tags as literal text on the attestation screen
            "content": ver["content"] or "", "content_format": ver["content_format"],
            "consent_text": tok["consent_text"],
            "signer_name": person["full_name"], "signer_email": person["email"]}


class SignIn(StrictModel):
    signer_name: str
    agree: bool = False


@router.post("/{token}")
def sign_submit(token: str, body: SignIn, request: Request):
    # Strip control chars / NUL / lone surrogates (Postgres text rejects NUL and can't encode
    # a lone surrogate → a crafted name would 500 this public route) and cap the length.
    name = "".join(c for c in (body.signer_name or "") if c.isprintable()).strip()[:200]
    if not name:
        raise HTTPException(400, "Please type your name to sign.")
    if not body.agree:
        raise HTTPException(400, "Please confirm you have read and agree before signing.")
    th, now = _hash(token), now_iso()
    st = t("signing_tokens")
    with engine.begin() as conn:
        # Race-free redemption: exactly one caller flips a LIVE token to consumed. A second
        # POST (or a concurrent one) matches no row and falls through to the 410 below.
        tok = conn.execute(text("""
            UPDATE signing_tokens SET consumed_at = :now
             WHERE token_hash = :h AND consumed_at IS NULL AND revoked_at IS NULL
               AND expires_at > :now
            RETURNING *"""), {"now": now, "h": th}).mappings().first()
        if tok is None:
            existing = conn.execute(select(st).where(st.c.token_hash == th)).mappings().first()
            if existing is None:
                raise HTTPException(404, "This signing link is not valid.")
            raise HTTPException(410, _GONE.get(_token_state(existing), "This link is no longer valid."))
        if tok["purpose"] != "ATTEST":
            raise HTTPException(400, "This link is not an attestation link.")
        sig, ver, doc, person = _load_attestation(conn, tok)
        if ver["status"] != "PUBLISHED":            # backstop: never attest superseded content
            raise HTTPException(410, "This version has been superseded — ask for a fresh signing link.")
        esig_id = str(uuid.uuid4())
        conn.execute(insert(t("electronic_signatures")).values(
            id=esig_id, tenant_id=tok["tenant_id"], signer_person_id=person["id"],
            signer_label=person["email"], signer_name=name, consent_text=tok["consent_text"],
            signer_ip=_client_ip(request), signer_user_agent=request.headers.get("user-agent"),
            file_sha256=ver["content_sha256"], signed_at=now))
        conn.execute(update(t("document_signatures")).where(
            t("document_signatures").c.id == sig["id"]).values(
            state="SIGNED", signed_at=now, e_signature_id=esig_id))
    return {"signed": True, "document_title": doc["title"],
            "version_label": ver["version_label"], "signed_at": now}
