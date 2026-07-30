"""Server-side HTML sanitisation for authored documents (P4-S4).

Documents are authored in a TipTap rich-text editor and stored as HTML. Stored HTML that is
later rendered into a browser is an XSS vector, and the editor is **not** the security
boundary — anyone can PATCH the endpoint directly. So every write path sanitises here.

Two design rules, both learned the hard way:

1. **The allow-list must be a superset of what the editor emits.** If it is narrower, a save
   silently destroys the author's formatting — tables collapse, alignment vanishes — with no
   error anywhere. That is worse than rejecting the save. `tests/test_html_sanitize.py`
   pins real TipTap output and asserts nothing is lost.
2. **`img` is deliberately absent.** xhtml2pdf resolves `<img src>` by calling
   `urllib.request.urlopen` server-side (xhtml2pdf/files.py), so a surviving remote image
   would turn publish-time PDF rendering into a server-side fetch of an author-controlled
   URL. Re-enabling images needs an owned-and-proxied image store, not a wider allow-list —
   see the note at the bottom of this module.

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

#: Everything TipTap's StarterKit + TableKit + TextAlign can emit. No `img` — see module docs.
ALLOWED_TAGS: set[str] = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "u", "s", "code", "pre", "blockquote",
    "ul", "ol", "li", "a",
    "table", "colgroup", "col", "tbody", "tr", "th", "td",
}

#: Per-tag attributes. `style` is admitted only where alignment/width is legitimate, and its
#: *contents* are then filtered by `FILTER_STYLE_PROPERTIES` — a whole-value regex would break
#: the moment TipTap changed its spacing.
ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "target"},          # NOT "rel" — ValueError while link_rel is set
    "ol": {"start", "type"},
    "code": {"class"},                # language-*; never pair with allowed_classes
    "table": {"style"},
    "col": {"style", "span"},
    "td": {"colspan", "rowspan", "colwidth", "style"},
    "th": {"colspan", "rowspan", "colwidth", "style"},
    "p": {"style"},
    **{f"h{i}": {"style"} for i in range(1, 7)},
}

#: Declarations kept inside a surviving `style=`. Everything else (position, background,
#: behaviour) is dropped. Case-sensitive in nh3 — content pasted from Word may arrive as
#: `TEXT-ALIGN` and lose its alignment. Acceptable; the alternative admits arbitrary CSS.
FILTER_STYLE_PROPERTIES: set[str] = {"text-align", "width", "min-width"}

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
    return value


def sanitize_document_html(html: str | None) -> str:
    """Clean authored HTML down to the allow-list. Idempotent."""
    return nh3.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        attribute_filter=_attribute_filter,
        filter_style_properties=FILTER_STYLE_PROPERTIES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


# ── re-enabling images, when there is an owned image store ──────────────────────────────
# 1. add "img" to ALLOWED_TAGS;
# 2. add "img": {"src", "alt", "title", "width", "height"} to ALLOWED_ATTRS;
# 3. add an img/src branch to _attribute_filter that returns the value ONLY for this app's
#    own image-URL shape, and resolve it to a local path before rendering.
# Do NOT simply widen URL_SCHEMES — that is the SSRF described in the module docstring.
# `tests/test_html_sanitize.py` has a test that fails loudly if `img` is re-admitted without
# a src filter.
