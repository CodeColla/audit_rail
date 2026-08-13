"""What this app will accept as an image, and what each consumer can actually eat (P6-S5).

Two jobs, in one module because they are the same question asked twice:

1. **`sniff()` — is this really an image, and which kind?** Moved here verbatim from
   `routers/org.py`, which had it first for the organisation logo. It is now also the gate on
   every image embedded in a controlled document, and having two copies of a security check
   is how the two drift apart. `org.py` imports it; its 400 messages are unchanged, so
   `tests/test_org_logo.py` passes untouched — which is the regression signal for this move.

2. **`to_docx_safe()` — python-docx cannot read WebP.** Its `docx/image/` package handles
   PNG, JPEG, GIF, BMP and TIFF only, so a WebP logo raises `UnrecognizedImageError` deep
   inside `add_picture`. We accept WebP on upload (it is a good format and browsers produce
   it), so the conversion has to happen somewhere; here, once, rather than at each call site.

The upload caps live here too, so the logo route and the document-image route cannot disagree
about what "too big" means without someone editing this file.
"""

from __future__ import annotations

import io

from fastapi import HTTPException

#: A logo is chrome, not evidence. 2 MB is generous for a raster mark and keeps a 40 MB
#: "logo" out of the vault and out of every page load.
MAX_LOGO_MB = 2

#: A document image is content — a network diagram or a screenshot of a console — so it gets
#: more room than a logo. Still bounded: at render time every embedded image is base64'd into
#: one HTML string held in memory by xhtml2pdf, so this number times the images in a document
#: is a real memory figure, not a disk figure.
MAX_IMAGE_MB = 5

#: magic bytes -> mime. Checked against the file's actual first bytes.
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]

#: The extension `storage.save` should use, derived from the SNIFFED type rather than from the
#: uploaded filename. A pasted screenshot arrives as "image.png" or with no name at all, and a
#: hostile one arrives as "invoice.pdf.png"; neither should decide what lands in the vault.
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg",
              "image/gif": ".gif", "image/webp": ".webp"}


def sniff(data: bytes) -> str:
    """The real format, from the bytes. Returns the mime type or raises a 400 saying why."""
    for sig, mime in _SIGNATURES:
        if data.startswith(sig):
            return mime
    # WebP is RIFF....WEBP — the marker sits at offset 8, so it needs its own check.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:400].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        raise HTTPException(400, "SVG images are not accepted — an SVG can carry scripts and "
                                 "this image is rendered inside the app. Please upload a PNG, "
                                 "JPEG or WebP.")
    raise HTTPException(400, "that does not look like an image — please upload a PNG, JPEG "
                             "or WebP file")


def dimensions(data: bytes) -> dict:
    """`{"width": int, "height": int}` in pixels, or `{}` if the image will not open.

    The editor uses this to insert a sane initial `width` attribute. Without it a 4000px
    screenshot goes into the document at its intrinsic size and blows out the PDF layout —
    the author sees it fitted to the editor's width and only finds out at publish."""
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(io.BytesIO(data)) as im:
            return {"width": im.width, "height": im.height}
    except Exception:
        return {}


def to_docx_safe(data: bytes, mime: str | None) -> bytes:
    """Bytes python-docx can embed. WebP becomes PNG; everything else passes through.

    Pillow is already installed as an xhtml2pdf dependency, so this costs no new package.
    Returns the input unchanged if the conversion fails — the caller is expected to treat
    `add_picture` raising as "render the placeholder instead", and an export must never fail
    because one picture was odd."""
    if mime != "image/webp":
        return data
    try:
        from PIL import Image  # noqa: PLC0415 — only needed on the WebP path

        out = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGBA").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return data
