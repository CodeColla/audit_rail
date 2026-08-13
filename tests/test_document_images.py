"""P6-S5 — images inside a controlled document.

`img` was off the sanitiser's allow-list for a reason that is written down in
`api/html_sanitize.py`: xhtml2pdf resolves `<img src>` with a server-side `urlopen`, so a
remote image turned *publishing* into a fetch of an author-controlled URL. Turning images on
therefore replaced one blunt control with several narrow ones, and this file is where each of
them is held to account:

  1. the sanitiser accepts one URL shape and nothing else (`test_html_sanitize.py`);
  2. the serving route is scoped by tenant AND by `files.purpose`;
  3. the render-time resolver is scoped by tenant, because the MARKDOWN branch is stored
     unsanitised and can therefore carry a hand-written id;
  4. every surviving src becomes a `data:` URI before xhtml2pdf sees it, so the code path
     that calls the network is unreachable rather than merely unused.

Take any one away and the feature is a vulnerability, so each has a test that fails loudly.
"""

import io
import re
import uuid

from sqlalchemy import text as sqltext

from tests.conftest import token

#: A real 1x1 PNG — python-docx and Pillow both parse the whole file, so magic bytes alone
#: would silently take the fail-soft path instead of proving anything.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63e095d20600008a005328b499f20000000049454e44"
    "ae426082")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _h(client, who="admin@kiam.example", pw="secret1"):
    return {"Authorization": f"Bearer {token(client, who, pw)}"}


def _setup(app_client):
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    ppl = []
    for i in range(2):
        pid = str(uuid.uuid4())
        with engine.begin() as c:
            c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                              "VALUES (:i,:t,:n,:e)"),
                      {"i": pid, "t": tid, "n": f"P{i}",
                       "e": f"p{i}-{uuid.uuid4().hex[:6]}@kiam.example"})
        ppl.append(pid)
    return _h(app_client), tid, ppl[0], ppl[1:]


def _doc(client, h, owner, content="<p>Body</p>"):
    r = client.post("/api/documents", headers=h, json={
        "title": "Information Security Policy", "owner_person_id": owner,
        "document_type": "POLICY", "content": content, "content_format": "HTML"})
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["version_id"]


def _upload(client, h, doc_id, data=PNG, name="d.png", mime="image/png"):
    return client.post(f"/api/documents/{doc_id}/images", headers=h,
                       files={"file": (name, data, mime)})


def _publish(client, h, approvers, doc_id, ver_id):
    client.post(f"/api/documents/{doc_id}/versions/{ver_id}/submit", headers=h,
                json={"threshold_required": 1, "approver_person_ids": approvers[:1]})
    appr = client.get(f"/api/documents/{doc_id}", headers=h).json()["approval"]
    client.post(f"/api/documents/approvals/{appr['id']}/decide", headers=h,
                json={"approver_person_id": approvers[0], "state": "APPROVED"})
    r = client.post(f"/api/documents/{doc_id}/versions/{ver_id}/publish", headers=h)
    assert r.status_code == 200, r.text


# ────────────────────────────────────────────── upload

def test_an_image_uploads_and_comes_back_with_its_canonical_url(app_client):
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)

    r = _upload(app_client, h, doc_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"] == f"/api/documents/images/{body['file_id']}"
    assert body["mime_type"] == "image/png"
    # dimensions come back so the editor can insert a sane width instead of letting a 4000px
    # screenshot decide the PDF layout for itself
    assert body["width"] == 1 and body["height"] == 1

    got = app_client.get(body["url"], headers=h)
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"].startswith("image/png")


def test_the_bytes_decide_the_type_not_the_upload(app_client):
    """Filename and Content-Type are both attacker-controlled; the first bytes are not."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    r = _upload(app_client, h, doc_id, name="invoice.pdf", mime="application/pdf")
    assert r.status_code == 201
    assert r.json()["mime_type"] == "image/png"


def test_an_svg_is_refused_with_a_reason(app_client):
    """Executable markup, rendered inside the app and walked by the export code. Refused out
    loud rather than silently dropped — the same call `org.py` made for the logo."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    r = _upload(app_client, h, doc_id, data=SVG, name="x.svg", mime="image/svg+xml")
    assert r.status_code == 400
    assert "SVG" in r.json()["detail"]


def test_non_images_and_oversize_uploads_are_refused(app_client):
    from api.rendering import imagefile

    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    assert _upload(app_client, h, doc_id, data=b"", name="e.png").status_code == 400
    assert _upload(app_client, h, doc_id, data=b"not an image at all").status_code == 400
    big = PNG + b"\x00" * (imagefile.MAX_IMAGE_MB * 1024 * 1024)
    assert _upload(app_client, h, doc_id, data=big).status_code == 413


def test_an_archived_document_takes_no_new_images(app_client):
    """`_require_active`, for the same reason every other content route calls it: an archived
    document stayed fully editable for a long time, and an upload is a content change."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    app_client.patch(f"/api/documents/{doc_id}", headers=h, json={"status": "ARCHIVED"})
    assert _upload(app_client, h, doc_id).status_code == 409


def test_an_image_cannot_be_uploaded_into_another_tenants_document(app_client):
    """`_doc` scopes by tenant, so a foreign doc_id is a 404 before any bytes are stored."""
    h, _tid, _owner, _ = _setup(app_client)
    other = app_client.post("/api/auth/signup", json={
        "full_name": "Other", "email": f"o-{uuid.uuid4().hex[:6]}@example.com",
        "password": "Passw0rdOne", "organisation_name": "Other Org"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    mine, _v = _doc(app_client, h, _owner)
    assert _upload(app_client, oh, mine).status_code == 404


# ────────────────────────────────────────────── serving: the two scoping rules

def test_the_image_route_cannot_read_another_tenants_image(app_client):
    """404, not 403 — a 403 confirms the id exists."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    mine = _upload(app_client, h, doc_id).json()["file_id"]

    other = app_client.post("/api/auth/signup", json={
        "full_name": "Other", "email": f"other-{uuid.uuid4().hex[:6]}@example.com",
        "password": "Passw0rdOne", "organisation_name": "Other Org"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    assert app_client.get(f"/api/documents/images/{mine}", headers=oh).status_code == 404


def test_the_image_route_cannot_be_used_to_read_evidence(app_client):
    """The regression test for a cross-module read, and the reason `files.purpose` exists.

    `files` is one shared vault — evidence, contracts, asset photos, templates and published
    PDFs all live in it — and most upload routes store `file.content_type`, the value the
    BROWSER claimed. So a route that authorised "any image in my tenant" by mime type could be
    steered: upload a confidential document declaring `Content-Type: image/png` with
    `evidence.add`, then read it back here holding only `documents.view`. Scoping on
    `purpose='DOC_IMAGE'` means this route can only reach rows its own sniffing upload made.
    """
    h, _tid, _owner, _ = _setup(app_client)
    ev = app_client.post("/api/evidence", headers=h, files={
        "file": ("secret.pdf", b"%PDF-1.4 confidential", "image/png")},
        data={"title": "Board minutes", "evidence_type": "POLICY"})
    assert ev.status_code in (200, 201), ev.text
    file_id = ev.json()["file_id"]

    r = app_client.get(f"/api/documents/images/{file_id}", headers=h)
    assert r.status_code == 404, "the document-image route served an evidence blob"


def test_viewing_an_image_needs_documents_view(app_client):
    h, _tid, owner, _ = _setup(app_client)
    doc_id, _v = _doc(app_client, h, owner)
    url = _upload(app_client, h, doc_id).json()["url"]
    assert app_client.get(url).status_code in (401, 403)


# ────────────────────────────────────────────── into the PDF and the DOCX

def test_a_referenced_image_is_embedded_in_the_pdf_without_any_fetch(app_client):
    """The src is replaced by a `data:` URI before xhtml2pdf runs, so its `FileNetworkManager`
    can only dispatch to `B64InlineURI` — a plain base64 decode. `NetworkFileUri` (urlopen)
    and `LocalFileURI` (opens any path) are unreachable, not merely unused."""
    h, _tid, owner, approvers = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    url = _upload(app_client, h, doc_id).json()["url"]
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f'<p>See below.</p><p><img src="{url}" alt="net" width="200"></p>',
        "content_format": "HTML"})

    pdf = app_client.get(f"/api/documents/{doc_id}/versions/{ver_id}/render.pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # A raster in a PDF is an XObject carrying `/Subtype /Image`.
    #
    # Two weaker assertions were tried first and both were worthless. Byte SIZE: a 459-byte
    # fixture moves the file by under a kilobyte, so any threshold is either unfalsifiable or
    # flaky. And bare `/Image`: every PDF reportlab writes contains it three times already,
    # in the ProcSet array `[/PDF /Text /ImageB /ImageC /ImageI]` — so that assertion passed
    # on a document with no picture in it at all. The negative case below is what caught it.
    assert b"/Subtype /Image" in pdf.content, "the image did not reach the PDF"
    plain_doc, plain_ver = _doc(app_client, h, owner)
    plain = app_client.get(
        f"/api/documents/{plain_doc}/versions/{plain_ver}/render.pdf", headers=h)
    assert b"/Subtype /Image" not in plain.content, "the marker must discriminate"

    docx = app_client.get(f"/api/documents/{doc_id}/versions/{ver_id}/render.docx", headers=h)
    from docx import Document
    d = Document(io.BytesIO(docx.content))
    assert len(d.inline_shapes) == 1, "the image did not become a real Word picture"
    assert "[image omitted" not in "\n".join(p.text for p in d.paragraphs)

    _publish(app_client, h, approvers, doc_id, ver_id)
    stored = app_client.get(f"/api/documents/{doc_id}/versions/{ver_id}/render.pdf", headers=h)
    assert stored.content[:4] == b"%PDF"


def test_the_stored_content_keeps_the_url_not_the_bytes(app_client):
    """Embedding happens at render time only. If base64 ever reached `content` it would land
    in `content_sha256`, be re-sent by the 1.2s autosave on every edit, and bury the version
    diff that approvers actually read."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    url = _upload(app_client, h, doc_id).json()["url"]
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f'<p><img src="{url}"></p>', "content_format": "HTML"})

    content = app_client.get(f"/api/documents/{doc_id}",
                             headers=h).json()["open_version"]["content"]
    assert url in content
    assert "data:image" not in content and "base64" not in content


def test_another_tenants_image_id_never_reaches_the_pdf(app_client):
    """The MARKDOWN branch is stored UNSANITISED on purpose — rewriting it would move
    `content_sha256`, which backs the e-signature — so an author can hand-write a foreign
    image id straight into a version. The resolver's tenant predicate is what stops it
    becoming another organisation's bytes inside a frozen, signed PDF."""
    other = app_client.post("/api/auth/signup", json={
        "full_name": "Victim", "email": f"victim-{uuid.uuid4().hex[:6]}@example.com",
        "password": "Passw0rdOne", "organisation_name": "Victim Org"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    from api.core.database import engine
    vpid = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email) "
                          "VALUES (:i,:t,'V',:e)"),
                  {"i": vpid, "t": other["tenant_id"], "e": f"v-{uuid.uuid4().hex[:6]}@x.example"})
    vdoc, _vv = _doc(app_client, oh, vpid)
    victim_image = _upload(app_client, oh, vdoc).json()["file_id"]

    h, _tid, owner, _ = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f"![x](/api/documents/images/{victim_image})",
        "content_format": "MARKDOWN"})

    from api.rendering import branding, render
    from api.routers.documents import image_resolver
    with engine.connect() as conn:
        tid = conn.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
        html = render.build_html(
            title="T", body_md=f"![x](/api/documents/images/{victim_image})",
            classification="INTERNAL", version_label="1.0", content_format="MARKDOWN",
            org=branding.letterhead(conn, tid)["org"],
            image_resolver=image_resolver(conn, tid))
    assert "<img" not in html, "another tenant's image was embedded"
    assert "data:image" not in html


def test_an_unresolvable_image_still_produces_a_document(app_client):
    """A vault object can go missing. The export degrades to a document without that picture;
    it does not 500 and it does not fall back to the placeholder PDF."""
    from api.rendering import render

    html_body = f'<p><img src="/api/documents/images/{uuid.uuid4()}"></p><p>Text survives.</p>'
    pdf, engine_name = render.render_pdf(
        title="T", body_md=html_body, classification="INTERNAL", version_label="1.0",
        content_format="HTML", image_resolver=lambda _fid: None)
    assert pdf[:4] == b"%PDF" and engine_name == "xhtml2pdf"


def test_a_remote_image_is_still_impossible_end_to_end(app_client):
    """The original guarantee, unchanged: no author-controlled URL survives to any renderer."""
    h, _tid, owner, _ = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": '<p><img src="https://evil.test/x.png"></p>', "content_format": "HTML"})
    content = app_client.get(f"/api/documents/{doc_id}",
                             headers=h).json()["open_version"]["content"]
    assert "evil.test" not in content and "<img" not in content

    for ext in ("pdf", "docx"):
        r = app_client.get(f"/api/documents/{doc_id}/versions/{ver_id}/render.{ext}", headers=h)
        assert r.status_code == 200
        assert b"evil.test" not in r.content


# ────────────────────────────────────────────── the public signing page

def _audience(client, h):
    """A fresh department with one person in it — a campaign targets an audience, and an
    audience with nobody in it issues no links."""
    from api.core.database import engine
    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    dept = f"SIGNERS-{uuid.uuid4().hex[:6]}"
    with engine.begin() as c:
        c.execute(sqltext("INSERT INTO people (id,tenant_id,full_name,email,department) "
                          "VALUES (:i,:t,'Signer',:e,:d)"),
                  {"i": str(uuid.uuid4()), "t": tid,
                   "e": f"s-{uuid.uuid4().hex[:6]}@kiam.example", "d": dept})
    return dept


def _attestation_link(client, h, doc_id, dept):
    """A campaign targets an AUDIENCE, not a person list — so the audience rule comes first.
    Mirrors `_campaign` in tests/test_attestation.py."""
    r = client.post(f"/api/documents/{doc_id}/audiences", headers=h,
                    json={"rules": [{"rule": "DEPARTMENT", "value": dept}]})
    assert r.status_code in (200, 201), r.text
    camp = client.post(f"/api/documents/{doc_id}/attestation-campaign", headers=h, json={})
    assert camp.status_code == 201, camp.text
    return camp.json()["issued"][0]["token"]


def test_a_logged_out_signer_can_see_the_policys_images(app_client):
    """The signing page is deliberately unauthenticated — a field engineer with no account
    opens a link and reads what they are being asked to attest to. If the diagrams did not
    load they would be signing something they cannot see."""
    h, _tid, owner, approvers = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    up = _upload(app_client, h, doc_id).json()
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f'<p><img src="{up["url"]}" alt="net"></p>', "content_format": "HTML"})
    _publish(app_client, h, approvers, doc_id, ver_id)
    dept = _audience(app_client, h)
    tok = _attestation_link(app_client, h, doc_id, dept)

    page = app_client.get(f"/api/sign/{tok}")
    assert page.status_code == 200, page.text
    # rewritten server-side, so the page itself needs no image code and no bearer token
    assert f"/api/sign/{tok}/images/{up['file_id']}" in page.json()["content"]
    assert "/api/documents/images/" not in page.json()["content"]

    img = app_client.get(f"/api/sign/{tok}/images/{up['file_id']}")
    assert img.status_code == 200 and img.content == PNG

    # …and looking at a picture must not consume the signature token
    assert app_client.get(f"/api/sign/{tok}").status_code == 200


def test_a_signing_token_is_not_a_read_of_every_image_in_the_tenant(app_client):
    """The check that makes the route safe: the id must appear in THIS version's frozen
    content. Tenant scoping alone would turn any live attestation link into a read of every
    document image the organisation has."""
    h, _tid, owner, approvers = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    shown = _upload(app_client, h, doc_id).json()
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f'<p><img src="{shown["url"]}"></p>', "content_format": "HTML"})
    _publish(app_client, h, approvers, doc_id, ver_id)

    elsewhere_doc, _ev = _doc(app_client, h, owner)
    elsewhere = _upload(app_client, h, elsewhere_doc).json()["file_id"]

    dept = _audience(app_client, h)
    tok = _attestation_link(app_client, h, doc_id, dept)
    assert app_client.get(f"/api/sign/{tok}/images/{shown['file_id']}").status_code == 200
    assert app_client.get(f"/api/sign/{tok}/images/{elsewhere}").status_code == 404
    assert app_client.get(f"/api/sign/{tok}/images/{uuid.uuid4()}").status_code == 404


def test_an_image_link_dies_with_its_token(app_client):
    h, _tid, owner, approvers = _setup(app_client)
    doc_id, ver_id = _doc(app_client, h, owner)
    up = _upload(app_client, h, doc_id).json()
    app_client.patch(f"/api/documents/{doc_id}/versions/{ver_id}", headers=h, json={
        "content": f'<p><img src="{up["url"]}"></p>', "content_format": "HTML"})
    _publish(app_client, h, approvers, doc_id, ver_id)
    dept = _audience(app_client, h)
    tok = _attestation_link(app_client, h, doc_id, dept)

    signed = app_client.post(f"/api/sign/{tok}",
                             json={"signer_name": "Signer", "agree": True})
    assert signed.status_code == 200, signed.text
    assert app_client.get(f"/api/sign/{tok}/images/{up['file_id']}").status_code == 410
    assert app_client.get(f"/api/sign/{uuid.uuid4().hex}/images/{up['file_id']}").status_code == 404
