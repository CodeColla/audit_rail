"""Document rendering: markdown -> HTML -> PDF (Sprint 2 / M9).

Renderer choice, and why: the sprint plan called for WeasyPrint but flagged its
system-lib dependency (cairo/pango) as a risk. It is not installable in every
environment, so this module is **pluggable and degrades gracefully**:

  1. WeasyPrint  — best fidelity, if importable (system libs present).
  2. xhtml2pdf   — pure-Python (reportlab), no system libs. The default that
                   actually runs here, so the flow is always testable.
  3. a minimal built-in PDF — last resort so `file_id` is never left empty.

The HTML is styled to look like a policy document (letterhead, version footer),
mirroring the on-screen render so the PDF matches what was approved.
"""

from __future__ import annotations

import html as _html
import io
import json
import re

from api.html_sanitize import sanitize_document_html

# ── markdown -> HTML ────────────────────────────────────────────────────────
try:
    import markdown as _md

    def md_to_html(text: str) -> str:
        return _md.markdown(text or "", extensions=["extra", "sane_lists", "tables"])
except Exception:  # pragma: no cover - markdown is a declared dep, this is defence
    def md_to_html(text: str) -> str:
        # extremely small fallback: paragraphs + headings + escaping
        out = []
        for block in (text or "").split("\n\n"):
            b = _html.escape(block.strip())
            m = re.match(r"(#{1,6})\s+(.*)", b)
            if m:
                lvl = len(m.group(1))
                out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>")
            elif b:
                out.append(f"<p>{b.replace(chr(10), '<br>')}</p>")
        return "\n".join(out)


class SheetFormatError(ValueError):
    """The stored SHEET content isn't the shape this module can render."""


ALIGNMENTS = ("left", "center", "right")
#: Cell font size, in points. Bounded because it is emitted into a style="" attribute and
#: because a 5000pt cell would blow up the PDF layout, not because Excel forbids it.
MIN_FONT_PT, MAX_FONT_PT = 6, 72
_COLOUR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ADDR_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")

#: Every style property we persist, and the validator each value must pass. This allow-list
#: IS the security boundary: `sheet_json_to_html` interpolates these into a style=""
#: attribute, so anything not validated here would be raw CSS injection into a document that
#: is later rendered to PDF (and, via xhtml2pdf, could reference external URLs). Adding a
#: property means adding a validator — never a passthrough.
_STYLE_PROPS: dict[str, object] = {
    "bold": lambda v: isinstance(v, bool),
    "italic": lambda v: isinstance(v, bool),
    "underline": lambda v: isinstance(v, bool),
    "align": lambda v: v in ALIGNMENTS,
    "fontSize": lambda v: isinstance(v, int) and not isinstance(v, bool)
                          and MIN_FONT_PT <= v <= MAX_FONT_PT,
    "color": lambda v: isinstance(v, str) and bool(_COLOUR_RE.match(v)),
    "background": lambda v: isinstance(v, str) and bool(_COLOUR_RE.match(v)),
}


def _cell_text(value) -> str:
    return "" if value is None else str(value)


def _blank_sheet(name: str = "Sheet1") -> dict:
    return {"name": name, "data": [], "formulas": {}, "style": {},
            "merges": {}, "colWidths": [], "colWrap": []}


def _parse_v1(obj: dict) -> dict:
    """Upcast the P5-S2 shape — `{data, bold, align}` — into the v2 structure.

    This path is PERMANENT, not a migration window. `freeze_published_version()`
    (db/schema.sql) makes a published version's `content` immutable and `content_sha256` is
    GENERATED over it, and that hash backs `electronic_signatures`. A v1 sheet that has been
    published therefore can never be rewritten on disk — so the reader has to understand it
    forever. Nothing here writes; it only reshapes in memory.
    """
    data = obj.get("data", [])
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        raise SheetFormatError("'data' must be a list of rows")
    for row in data:
        if not all(isinstance(c, (str, int, float)) or c is None for c in row):
            raise SheetFormatError("every cell must be text, a number, or null")
    bold = obj.get("bold", [])
    if not isinstance(bold, list) or not all(isinstance(a, str) for a in bold):
        raise SheetFormatError("'bold' must be a list of cell addresses")
    align = obj.get("align", {})
    if not isinstance(align, dict) or not all(
            isinstance(k, str) and v in ALIGNMENTS for k, v in align.items()):
        raise SheetFormatError(f"'align' values must be one of {', '.join(ALIGNMENTS)}")

    style: dict[str, dict] = {}
    for addr in bold:
        style.setdefault(addr, {})["bold"] = True
    for addr, a in align.items():
        style.setdefault(addr, {})["align"] = a
    sheet = _blank_sheet()
    sheet["data"] = [[_cell_text(c) for c in row] for row in data]
    sheet["style"] = style
    return {"version": 1, "sheets": [sheet]}


def _parse_sheet_obj(raw, index: int) -> dict:
    where = f"sheet {index + 1}"
    if not isinstance(raw, dict):
        raise SheetFormatError(f"{where} must be an object")
    out = _blank_sheet(str(raw.get("name") or f"Sheet{index + 1}"))

    data = raw.get("data", [])
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        raise SheetFormatError(f"{where}: 'data' must be a list of rows")
    for row in data:
        if not all(isinstance(c, (str, int, float)) or c is None for c in row):
            raise SheetFormatError(f"{where}: every cell must be text, a number, or null")
    out["data"] = [[_cell_text(c) for c in row] for row in data]

    formulas = raw.get("formulas", {})
    if not isinstance(formulas, dict):
        raise SheetFormatError(f"{where}: 'formulas' must be an object keyed by cell")
    for addr, expr in formulas.items():
        if not (isinstance(addr, str) and _ADDR_RE.match(addr)):
            raise SheetFormatError(f"{where}: '{addr}' is not a cell address")
        if not (isinstance(expr, str) and expr.startswith("=")):
            raise SheetFormatError(f"{where}: formula at {addr} must be a string starting '='")
    out["formulas"] = dict(formulas)

    style = raw.get("style", {})
    if not isinstance(style, dict):
        raise SheetFormatError(f"{where}: 'style' must be an object keyed by cell")
    for addr, props in style.items():
        if not (isinstance(addr, str) and _ADDR_RE.match(addr)):
            raise SheetFormatError(f"{where}: '{addr}' is not a cell address")
        if not isinstance(props, dict):
            raise SheetFormatError(f"{where}: style at {addr} must be an object")
        for key, val in props.items():
            check = _STYLE_PROPS.get(key)
            if check is None:
                raise SheetFormatError(
                    f"{where}: unsupported style '{key}' at {addr} — allowed: "
                    f"{', '.join(sorted(_STYLE_PROPS))}")
            if not check(val):  # type: ignore[operator]
                raise SheetFormatError(f"{where}: invalid value for '{key}' at {addr}")
    out["style"] = {a: dict(p) for a, p in style.items()}

    merges = raw.get("merges", {})
    if not isinstance(merges, dict):
        raise SheetFormatError(f"{where}: 'merges' must be an object keyed by cell")
    for addr, span in merges.items():
        if not (isinstance(addr, str) and _ADDR_RE.match(addr)):
            raise SheetFormatError(f"{where}: '{addr}' is not a cell address")
        if not isinstance(span, dict):
            raise SheetFormatError(f"{where}: merge at {addr} must be an object")
        for k in ("colspan", "rowspan"):
            v = span.get(k, 1)
            if not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000):
                raise SheetFormatError(f"{where}: merge {k} at {addr} must be 1..1000")
    out["merges"] = {a: {"colspan": int(s.get("colspan", 1)),
                         "rowspan": int(s.get("rowspan", 1))} for a, s in merges.items()}

    widths = raw.get("colWidths", [])
    if not isinstance(widths, list) or not all(
            isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w <= 2000
            for w in widths):
        raise SheetFormatError(f"{where}: 'colWidths' must be numbers between 0 and 2000")
    out["colWidths"] = [float(w) for w in widths]

    # Text wrapping is per COLUMN, not per cell. That is a deliberate consequence of how
    # jspreadsheet works: a per-cell `white-space` set through setStyle is wiped by its own
    # updateCell the next time that cell's value changes (verified in a browser), whereas
    # `columns[i].wordWrap` is consulted on every render and therefore survives editing.
    # It also suits a register, where one long "Description" column wraps and the rest don't.
    wrap = raw.get("colWrap", [])
    if not isinstance(wrap, list) or not all(isinstance(w, bool) for w in wrap):
        raise SheetFormatError(f"{where}: 'colWrap' must be a list of true/false")
    out["colWrap"] = list(wrap)
    return out


def parse_sheet(content: str) -> dict:
    """Parse and validate a SHEET version's stored JSON, in either supported shape.

    **v2** (P5-S2b, current) — a workbook::

        {"version": 2, "sheets": [{
            "name": "Sheet1",
            "data": [["Control","Owner"], ["MFA","Alice"]],  # COMPUTED values
            "formulas": {"C2": "=SUM(A2:B2)"},               # formula SOURCE
            "style": {"A1": {"bold": true, "align": "center", "fontSize": 12,
                             "color": "#1a2432", "background": "#eef0f2"}},
            "merges": {"A1": {"colspan": 2, "rowspan": 1}},
            "colWidths": [120, 100]}]}

    **v1** (P5-S2) — `{"data": …, "bold": […], "align": {…}}`, upcast by `_parse_v1`. That
    path is permanent; see its docstring for why published rows can never be migrated.

    **Why `data` holds computed values and `formulas` holds the source.** This module and
    `docx_export` are Python and have no formula engine, so if only `=SUM(A2:B2)` were
    stored, every export would print the expression instead of the number. Storing the
    evaluated result is also precisely what makes a published sheet honest: the browser
    evaluates on save, the value lands in `data`, and publishing freezes it — so `TODAY()`
    renders forever as the date the version was approved, not as the reader's today, and a
    signed document always shows the numbers its approvers actually signed.

    Returns a normalised `{"version": int, "sheets": [ … ]}` with every sheet key present, so
    callers never need `.get()` defaults. Raises `SheetFormatError` with a message safe to
    surface in a 400 — never lets a malformed grid reach a raw KeyError/TypeError.
    """
    try:
        obj = json.loads(content or "{}")
    except json.JSONDecodeError as e:
        raise SheetFormatError(f"not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise SheetFormatError("must be a JSON object")

    # An empty object is a brand-new sheet, not an error.
    if not obj:
        return {"version": 2, "sheets": [_blank_sheet()]}

    version = obj.get("version")
    if version is None:
        # No discriminator => the v1 shape. Reject anything that is neither, rather than
        # silently rendering an empty grid for a typo'd key.
        if "data" not in obj and "bold" not in obj and "align" not in obj:
            raise SheetFormatError("missing 'version' (v2) and no v1 'data' key")
        return _parse_v1(obj)
    if version == 1:
        return _parse_v1(obj)
    if version != 2:
        raise SheetFormatError(f"unsupported sheet format version {version!r}")

    sheets = obj.get("sheets", [])
    if not isinstance(sheets, list) or not sheets:
        raise SheetFormatError("'sheets' must be a non-empty list")
    return {"version": 2, "sheets": [_parse_sheet_obj(s, i) for i, s in enumerate(sheets)]}


def _col_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA — spreadsheet column addressing."""
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell_css(props: dict) -> str:
    """Style properties -> a CSS declaration string. Every value has already been validated
    by `_STYLE_PROPS`; this only decides how to spell it."""
    decls = []
    if props.get("align"):
        decls.append(f"text-align:{props['align']}")
    if props.get("fontSize"):
        decls.append(f"font-size:{props['fontSize']}pt")
    if props.get("color"):
        decls.append(f"color:{props['color']}")
    if props.get("background"):
        # `background-color`, not the `background` shorthand — xhtml2pdf handles the
        # longhand reliably and the shorthand can swallow the value silently.
        decls.append(f"background-color:{props['background']}")
    return ";".join(decls)


def _sheet_to_table(sheet: dict) -> str:
    """One worksheet -> one HTML <table>."""
    wrap_cols = sheet.get("colWrap") or []
    rows_html = []
    for r, row in enumerate(sheet["data"]):
        cells = []
        for c, cell in enumerate(row):
            addr = f"{_col_letter(c)}{r + 1}"
            props = sheet["style"].get(addr, {})
            text = _html.escape(cell)
            # <strong>/<em>/<u>, not <th> — bold here means "this cell's text is bold," not
            # "this is a header cell." <th> would be a semantic mismatch (bold can be
            # anywhere in the grid) and would also make docx_export._table() treat an
            # all-bold first row as a page-repeating header, which isn't what this means.
            if props.get("bold"):
                text = f"<strong>{text}</strong>"
            if props.get("italic"):
                text = f"<em>{text}</em>"
            if props.get("underline"):
                text = f"<u>{text}</u>"
            attrs = ""
            css = _cell_css(props)
            # Additive by design: a wrapped column gains `pre-wrap`; an unwrapped one is left
            # exactly as it rendered before this feature existed. Emitting `nowrap` for the
            # unwrapped case would match the editor more closely, but it would change how
            # already-published — frozen, hashed, signed — documents render.
            if c < len(wrap_cols) and wrap_cols[c]:
                css = f"{css};white-space:pre-wrap" if css else "white-space:pre-wrap"
            if css:
                attrs += f' style="{css}"'
            span = sheet["merges"].get(addr)
            if span:
                if span["colspan"] > 1:
                    attrs += f' colspan="{span["colspan"]}"'
                if span["rowspan"] > 1:
                    attrs += f' rowspan="{span["rowspan"]}"'
            cells.append(f"<td{attrs}>{text}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows_html)}</table>" if rows_html else "<table></table>"


def sheet_diff_lines(content: str) -> list[str]:
    """Flatten a workbook to one comparable line per non-empty cell, for the version diff.

    Without this a SHEET version diffs as a single line of JSON, so every change reports
    exactly "1 added, 1 removed" — a diff that can never be wrong and is therefore useless.
    The same reasoning as the HTML projection in `documents._diff_lines`.

    Each line is `Sheet!A1: value  [=FORMULA]  {bold,center}` — cheap to read, and stable
    under edits elsewhere in the grid, so a one-cell change shows as a one-line change.
    Empty cells are skipped: a default sheet is 12x30, and 360 blank rows of noise would
    bury the handful of lines that actually differ.

    The sheet prefix is omitted for a single-sheet workbook, so those diffs stay terse.
    """
    book = parse_sheet(content)
    multi = len(book["sheets"]) > 1
    lines: list[str] = []
    for sheet in book["sheets"]:
        prefix = f"{sheet['name']}!" if multi else ""
        for r, row in enumerate(sheet["data"]):
            for c, cell in enumerate(row):
                addr = f"{_col_letter(c)}{r + 1}"
                formula = sheet["formulas"].get(addr)
                props = sheet["style"].get(addr, {})
                if not cell and not formula and not props:
                    continue
                line = f"{prefix}{addr}: {cell}"
                if formula:
                    line += f"  [{formula}]"
                if props:
                    # Formatting is content in a controlled document — a cell going bold, or
                    # a total turning red, is a real change an approver should see in the
                    # diff rather than have it read as "no change".
                    flags = ",".join(f"{k}={v}" if not isinstance(v, bool) else k
                                     for k, v in sorted(props.items()) if v)
                    if flags:
                        line += f"  {{{flags}}}"
                lines.append(line)
    return lines


def sheet_json_to_html(content: str) -> str:
    """The one place a SHEET's stored JSON becomes markup. Both the PDF and DOCX export
    paths route through this, then through their EXISTING html-table handling — `build_html`
    already sanitises whatever HTML it's given, and `docx_export._Walker._table()` already
    turns an HTML `<table>` into a Word table — so a spreadsheet needs no export code of its
    own beyond this one conversion.

    A multi-sheet workbook renders as one titled table per worksheet. The heading is emitted
    only when there is more than one, so a single-sheet document (the overwhelmingly common
    case, and every v1 document) is byte-for-byte what it was before."""
    book = parse_sheet(content)
    sheets = book["sheets"]
    if len(sheets) == 1:
        return _sheet_to_table(sheets[0])
    parts = []
    for i, sheet in enumerate(sheets):
        # Page-break before every sheet after the first: a workbook exported to PDF should
        # read as one worksheet per page, the way Excel prints it.
        brk = ' style="page-break-before:always"' if i else ""
        parts.append(f"<h2{brk}>{_html.escape(sheet['name'])}</h2>{_sheet_to_table(sheet)}")
    return "".join(parts)


_PAGE_CSS = """
@page { size: A4; margin: 22mm 20mm; }
body { font-family: 'Helvetica','Arial',sans-serif; font-size: 11pt; color: #1a2432; line-height: 1.5; }
h1 { font-size: 20pt; color: #0E1A2B; margin: 0 0 4pt; }
h2 { font-size: 15pt; color: #0E1A2B; margin: 16pt 0 4pt; }
h3 { font-size: 12.5pt; color: #0E1A2B; margin: 12pt 0 3pt; }
.hdr { border-bottom: 2px solid #0E1A2B; padding-bottom: 8pt; margin-bottom: 16pt; }
.hdr .org { font-weight: bold; font-size: 13pt; color: #0E1A2B; }
.hdr .meta { color: #5B6573; font-size: 9pt; }
.cls { display: inline-block; border: 1px solid #5B6573; color: #5B6573; border-radius: 3pt;
       padding: 1pt 5pt; font-size: 8pt; letter-spacing: 0.5pt; }
code { background: #eef0f2; padding: 1pt 3pt; border-radius: 2pt; font-family: monospace; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #D5D9DE; padding: 4pt 6pt; text-align: left; font-size: 10pt; }
.foot { margin-top: 20pt; border-top: 1px solid #D5D9DE; padding-top: 6pt;
        color: #9AA1AB; font-size: 8pt; }
"""


DEFAULT_ORG = "KIAM INTL PVT LTD"


def doc_meta(*, title: str, classification: str, version_label: str,
             org: str = DEFAULT_ORG, status: str = "DRAFT") -> dict:
    """The letterhead facts, shared by the PDF and the DOCX so the two agree."""
    return {
        "org": org, "title": title, "classification": classification,
        "version_label": version_label, "status": status,
        "header_line": f"{title} · v{version_label} · {status}",
        "footer_line": (f"{org} · {title} v{version_label} · Classification: "
                        f"{classification} · This document is controlled; "
                        f"printed copies are uncontrolled."),
    }


def build_html(*, title: str, body_md: str, classification: str, version_label: str,
               org: str = DEFAULT_ORG, status: str = "DRAFT",
               content_format: str = "MARKDOWN") -> str:
    # P4-S4: authored content is HTML now, but everything written earlier is markdown and
    # stays that way. Feeding HTML through md_to_html is NOT identity — python-markdown
    # reflows it — so the branch is required, not merely an optimisation.
    if content_format == "SHEET":
        body = sheet_json_to_html(body_md)
    elif content_format == "HTML":
        body = body_md or ""
    else:
        body = md_to_html(body_md)
    # Sanitise the RENDERED html, whatever its source. HTML content was already cleaned on
    # write (this is idempotent and cheap), but markdown is deliberately stored raw — and
    # python-markdown both passes inline HTML straight through and turns ![](url) into an
    # <img>. xhtml2pdf resolves image srcs with a server-side urllib urlopen (see
    # xhtml2pdf/files.py), so without this a markdown policy could make *publishing* fetch
    # an author-controlled URL. Sanitising only the HTML branch left that wide open.
    body = sanitize_document_html(body)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_PAGE_CSS}</style></head><body>
<div class="hdr">
  <span class="org">{_html.escape(org)}</span>
  <span class="cls" style="float:right">{_html.escape(classification)}</span>
  <div class="meta">{_html.escape(title)} &middot; v{version_label} &middot; {status}</div>
</div>
<h1>{_html.escape(title)}</h1>
{body}
<div class="foot">{_html.escape(org)} &middot; {_html.escape(title)} v{version_label}
&middot; Classification: {_html.escape(classification)} &middot; This document is controlled;
printed copies are uncontrolled.</div>
</body></html>"""


def _weasyprint(html: str) -> bytes | None:
    try:
        import weasyprint  # noqa: PLC0415
        return weasyprint.HTML(string=html).write_pdf()
    except Exception:
        return None


def _xhtml2pdf(html: str) -> bytes | None:
    try:
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        status = pisa.CreatePDF(io.StringIO(html), dest=buf)
        return None if status.err else buf.getvalue()
    except Exception:
        return None


def _minimal_pdf(title: str) -> bytes:
    """A valid one-page PDF so file_id is never empty even with no renderer."""
    text = f"{title} (PDF renderer unavailable — install markdown + xhtml2pdf)"
    stream = f"BT /F1 12 Tf 72 760 Td ({text[:90]}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, o))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
              % (len(objs) + 1, xref))
    return out.getvalue()


def render_pdf(*, title: str, body_md: str, classification: str, version_label: str,
               status: str = "DRAFT", content_format: str = "MARKDOWN") -> tuple[bytes, str]:
    """Return (pdf_bytes, engine_name)."""
    html = build_html(title=title, body_md=body_md, classification=classification,
                      version_label=version_label, status=status,
                      content_format=content_format)
    for name, fn in (("weasyprint", _weasyprint), ("xhtml2pdf", _xhtml2pdf)):
        data = fn(html)
        if data and data[:4] == b"%PDF":
            return data, name
    return _minimal_pdf(title), "minimal"
