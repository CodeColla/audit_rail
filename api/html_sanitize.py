"""Server-side HTML sanitisation for authored documents (P4-S4).

Documents are authored in a TipTap rich-text editor and stored as HTML. Stored HTML that is
later rendered into a browser is an XSS vector, and the editor is **not** the security
boundary — anyone can PATCH the endpoint directly. So every write path sanitises here.

Two design rules, both learned the hard way:

1. **The allow-list must be a superset of what the editor emits.** If it is narrower, a save
   silently destroys the author's formatting — tables collapse, alignment vanishes — with no
   error anywhere. That is worse than rejecting the save. `tests/test_html_sanitize.py`
   pins real TipTap output and asserts nothing is lost.
2. **`img` is admitted only for this app's own image URLs (P6-S5).** xhtml2pdf resolves
   `<img src>` by calling `urllib.request.urlopen` server-side (xhtml2pdf/files.py), so a
   remote image would turn publish-time PDF rendering into a server-side fetch of an
   author-controlled URL. That is why `img` was off the list entirely until there was an
   owned image store; now that there is one, the control is `IMG_SRC_RE` — an anchored match
   on our own route shape — and **not** a wider `URL_SCHEMES`. Widening `URL_SCHEMES` is the
   SSRF; adding `data:` there would be the same mistake wearing a different hat.

   Two layers back this up, because a sanitiser should not be the only thing standing between
   an author and `urlopen`: `render._embed_images` rewrites every surviving src to a `data:`
   URI before xhtml2pdf ever sees the HTML, so the network-fetching code path is unreachable
   by construction rather than by policy.

nh3 is the maintained Rust (ammonia) successor to bleach, which is deprecated and points at
it. Two of its edges bite hard and are encoded below rather than rediscovered:
  * passing `allowed_classes` alongside a custom `attributes` dict raises a
    `pyo3_runtime.PanicException`, which does **not** inherit from `Exception` and therefore
    slips straight through `except Exception`;
  * naming `rel` in `attributes["a"]` while `link_rel` is set raises `ValueError` at call
    time.
Both are avoided by filtering in `_attribute_filter` instead.
"""

from __future__ import annotations

import re

import nh3

#: Everything TipTap's StarterKit + TableKit + TextAlign + our own image node can emit.
ALLOWED_TAGS: set[str] = {
    "p", "br", "hr", "img",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "u", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "a",
    "table", "colgroup", "col", "tbody", "tr", "th", "td",
}

#: The ONLY image src this application will store, and the whole security boundary for images.
#:
#: Anchored at both ends and hex-only, which is what rules out every variant that matters: no
#: scheme (`https://evil.test`), no protocol-relative `//evil.test`, no `..` traversal, no
#: query or fragment smuggling a second URL, no uppercase host trick. `files.id` is written as
#: `str(uuid.uuid4())` / `gen_random_uuid()::text`, both lowercase and hyphenated, so a strict
#: uuid shape costs nothing and rejects everything else.
#:
#: `\Z`, NOT `$`. In Python `$` also matches immediately before a trailing newline, so
#: `IMG_SRC_RE.match(src + "\n")` succeeded and a src with a smuggled newline survived an
#: allegedly anchored regex. Caught by hammering this list before anything was built on it.
#: The same applies to `_IMG_DIM` — `"640\n"` is not a width.
IMG_SRC_RE = re.compile(
    r"^/api/documents/images/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\Z")

#: 1..9999. Bounded because these land in the PDF layout and a `width="99999999"` is a way to
#: make a published policy unreadable.
_IMG_DIM = re.compile(r"^[1-9][0-9]{0,3}\Z")

#: An `<img>` with no `src` left after filtering — i.e. one whose src we refused.
#:
#: nh3 cannot delete an ELEMENT from an attribute filter, only an attribute, so a foreign
#: image would otherwise survive as a bare `<img>`. That is inert everywhere (browsers render
#: nothing, xhtml2pdf guards on `if attr.src:`), but "no foreign image survives in any form"
#: is a far easier invariant to keep true than "it survives but is harmless", and it keeps the
#: long-standing `"<img" not in html` assertions meaningful.
#:
#: Applied to nh3's OUTPUT, never to raw input: that output is canonical — attributes are
#: always double-quoted and values always escaped — so a regex is exact there. Running a
#: second HTML parser over hostile input just to delete a tag would add attack surface to
#: remove some.
_SRCLESS_IMG = re.compile(r"<img(?![^>]*\ssrc=)[^>]*>", re.I)

#: Per-tag attributes. `style` is admitted only where alignment/width is legitimate, and its
#: *contents* are then filtered by `FILTER_STYLE_PROPERTIES` — a whole-value regex would break
#: the moment TipTap changed its spacing.
ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "target"},          # NOT "rel" — ValueError while link_rel is set
    # NOT "style" on img: width/height are enough, and admitting style here would put the
    # FILTER_STYLE_PROPERTIES surface onto the one element xhtml2pdf lays out specially.
    "img": {"src", "alt", "title", "width", "height"},
    "ol": {"start", "type"},
    "code": {"class"},                # language-*; never pair with allowed_classes
    "table": {"style"},
    "col": {"style", "span"},
    "td": {"colspan", "rowspan", "colwidth", "style"},
    "th": {"colspan", "rowspan", "colwidth", "style"},
    "p": {"style"},
    **{f"h{i}": {"style"} for i in range(1, 7)},
}

#: Declarations kept inside a surviving `style=`. Everything else (position, behaviour,
#: `background-image`) is dropped. Case-sensitive in nh3 — content pasted from Word may
#: arrive as `TEXT-ALIGN` and lose its alignment. Acceptable; the alternative admits
#: arbitrary CSS.
#:
#: P5-S2b added the last three for spreadsheet cells. Without them a coloured or resized cell
#: rendered correctly in the editor and then silently lost its formatting in the PDF/DOCX —
#: the worst kind of bug, because nothing reports it. They are safe to admit: none can
#: execute script, and only the `background-color` LONGHAND is allowed, never the
#: `background` shorthand, which can carry `url(…)` and would hand xhtml2pdf a server-side
#: fetch (the same class of hole the img/urlopen note in render.build_html describes).
#: Values reaching here from a SHEET are additionally pre-validated against
#: `render._STYLE_PROPS` (hex colours only, font size bounded) before they are ever written.
FILTER_STYLE_PROPERTIES: set[str] = {
    "text-align", "width", "min-width",
    "font-size", "color", "background-color",
    # P5-S2c: spreadsheet columns marked "wrap text". Purely presentational, no URL, no
    # script; the value is emitted by our own renderer, never taken from author input.
    "white-space",
}

#: Absolute-URL schemes for `href`. `javascript:` and `data:` are absent by construction.
URL_SCHEMES: set[str] = {"http", "https", "mailto"}

_LANG_CLASS = re.compile(r"^language-[A-Za-z0-9_+#.-]{1,32}$")
_COLWIDTH = re.compile(r"^\d{1,5}(,\d{1,5}){0,49}$")
_OL_TYPES = frozenset("1aAiI")


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """Per-attribute value filter. Returning None drops the attribute."""
    if tag == "code" and attr == "class":
        # keep only a single well-formed language token, discarding anything else present
        return next((tok for tok in value.split() if _LANG_CLASS.match(tok)), None)
    if attr == "colwidth":
        return value if _COLWIDTH.match(value) else None
    if tag == "a" and attr == "target":
        return "_blank" if value == "_blank" else None
    if tag == "ol" and attr == "type":
        return value if value in _OL_TYPES else None
    if tag == "img":
        if attr == "src":
            return value if IMG_SRC_RE.match(value) else None
        if attr in ("width", "height"):
            return value if _IMG_DIM.match(value) else None
    return value


def sanitize_document_html(html: str | None) -> str:
    """Clean authored HTML down to the allow-list. Idempotent."""
    cleaned = nh3.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        attribute_filter=_attribute_filter,
        filter_style_properties=FILTER_STYLE_PROPERTIES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    # Second pass, on our own canonical output: drop the `<img>` shells left behind when
    # `_attribute_filter` refused a src. See `_SRCLESS_IMG` for why this is not done with a
    # parser, and why "no foreign image survives at all" is the invariant worth having.
    return _SRCLESS_IMG.sub("", cleaned)


# ── images: done in P6-S5, following the checklist that used to live here ───────────────
# The three steps this note prescribed — `img` on ALLOWED_TAGS, a narrow attribute set, and
# an img/src branch matching only this app's own URL shape — are implemented above, plus a
# fourth the note did not anticipate: nh3 cannot delete an element from an attribute filter,
# so `_SRCLESS_IMG` removes the shells left behind.
#
# The rule the note existed to protect is unchanged and still the important one:
#   DO NOT widen URL_SCHEMES. Not for `data:`, not for anything. The src allow-list is
#   IMG_SRC_RE, and `render._embed_images` resolves those to bytes we already hold, so
#   xhtml2pdf's network path stays unreachable rather than merely unused.
