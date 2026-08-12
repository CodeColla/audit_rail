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

import base64
import html as _html
import io
import json
import re

from api.html_sanitize import IMG_SRC_RE, sanitize_document_html

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

#: The currencies a column can be formatted in (P6-S4). Deliberately a closed list rather
#: than a free-text symbol: the code travels into an .xlsx number-format string and into the
#: PDF, so an unvalidated symbol would be another string interpolated into a rendered
#: document. Authors pick per column, which is why there is no org-wide currency setting —
#: one workbook can hold a rupee column beside a dollar one, which a vendor register needs.
CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
_FORMAT_RE = re.compile(r"^(?:percent|currency:(?:%s))$" % "|".join(CURRENCY_SYMBOLS))

#: What counts as a number for formatting purposes. Deliberately NOT `float()`: Python
#: accepts "1_000", "nan" and "inf", and JavaScript's `Number()` does not — and the browser
#: grid, this renderer and `DocBody.tsx` must agree on which cells get formatted or the
#: editor and the PDF would disagree. This regex is the shared definition; `NUMERIC_RE` in
#: `webui/src/lib/sheetFormat.ts` is its twin.
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

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


#: A cell comment is a reviewer's note, not policy text. Bounded because it is content that
#: rides along in a hashed, signed column and appears in the version diff — a 50kB essay in one
#: cell would bury the diff the way a base64 image would.
MAX_COMMENT_CHARS = 2000


def _blank_sheet(name: str = "Sheet1") -> dict:
    return {"name": name, "data": [], "formulas": {}, "style": {},
            "merges": {}, "colWidths": [], "colWrap": [], "colFormat": [], "comments": {}}


def format_cell_value(text: str, fmt: str) -> str:
    """Apply a column's number format to one stored cell value (P6-S4).

    **Text falls through unchanged**, which is the whole reason this is a function and not an
    f-string at the call site. A register's header lives in the same column as its figures,
    so "Amount" under a currency format must stay "Amount" rather than becoming a bare "₹" —
    and that is also exactly what Excel does, where a number format applies to numbers and
    text passes through. `SheetEditor.tsx`'s column `render` hook and `DocBody.tsx` apply the
    identical rule, so the editor, the read view and the PDF agree cell for cell.

    **Percent follows Excel's convention: the stored number is a ratio**, so 0.15 reads as
    15.00%. Chosen over "15 means 15%" because `0.00%` in the exported .xlsx multiplies by
    100 whatever we do — deviating here would make the workbook disagree with the PDF.
    """
    if not fmt or not _NUMERIC_RE.match(text or ""):
        return text
    value = float(text)
    if fmt == "percent":
        return f"{value * 100:,.2f}%"
    symbol = CURRENCY_SYMBOLS.get(fmt.split(":", 1)[1], "")
    # Sign OUTSIDE the symbol ("-₹5.00", not "₹-5.00") — the accounting convention, and the
    # same spelling `formatCell` in sheetFormat.ts produces.
    return f"-{symbol}{abs(value):,.2f}" if value < 0 else f"{symbol}{value:,.2f}"


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

    # Number format is per COLUMN for the same reason wrap is, and it is not a `_STYLE_PROPS`
    # entry: jspreadsheet reads formatting off `options.columns[i]` (its `render` hook takes
    # the column config as an argument and there is no per-cell equivalent), so a per-cell
    # format would be a setting the editor could never actually honour. It also suits a
    # register, where "Annual cost" is a column.
    fmt = raw.get("colFormat", [])
    if not isinstance(fmt, list) or not all(
            isinstance(f, str) and (f == "" or _FORMAT_RE.match(f)) for f in fmt):
        raise SheetFormatError(
            f"{where}: 'colFormat' entries must be \"\", 'percent', or 'currency:' plus one "
            f"of {', '.join(sorted(CURRENCY_SYMBOLS))}")
    out["colFormat"] = list(fmt)

    # Per-CELL, unlike wrap and number format — a comment is about one cell, and jspreadsheet's
    # comment API is per cell too (`setComments`), so nothing forces it up to the column here.
    comments = raw.get("comments", {})
    if not isinstance(comments, dict):
        raise SheetFormatError(f"{where}: 'comments' must be an object keyed by cell")
    for addr, note in comments.items():
        if not (isinstance(addr, str) and _ADDR_RE.match(addr)):
            raise SheetFormatError(f"{where}: '{addr}' is not a cell address")
        if not isinstance(note, str):
            raise SheetFormatError(f"{where}: the comment at {addr} must be text")
        if len(note) > MAX_COMMENT_CHARS:
            raise SheetFormatError(
                f"{where}: the comment at {addr} is longer than {MAX_COMMENT_CHARS} characters")
    # Empty strings mean "no comment" — jspreadsheet clears a comment by setting "" — so they
    # are dropped rather than stored, keeping an uncommented sheet's JSON identical to before.
    out["comments"] = {a: n for a, n in comments.items() if n}
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
            "colWidths": [120, 100],
            "colWrap": [false, true],                        # per COLUMN, not per cell
            "colFormat": ["currency:INR", "percent"]}]}      # ditto — see `_parse_sheet_obj`

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
    fmt_cols = sheet.get("colFormat") or []
    rows_html = []
    for r, row in enumerate(sheet["data"]):
        cells = []
        for c, cell in enumerate(row):
            addr = f"{_col_letter(c)}{r + 1}"
            props = sheet["style"].get(addr, {})
            # Format for DISPLAY only — `data` keeps the raw number, so the same cell can be
            # re-rendered in another currency, or none, without the value having been lost.
            text = _html.escape(format_cell_value(
                cell, fmt_cols[c] if c < len(fmt_cols) else ""))
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
        # A column's number format is content, not decoration: flipping a column from
        # currency to percent changes every figure a reader sees while leaving `data`
        # byte-identical, so without these lines that change would diff as "no change".
        # Emitted per column, addressed by letter, because that is where the format lives.
        for c, fmt in enumerate(sheet.get("colFormat") or []):
            if fmt:
                lines.append(f"{prefix}column {_col_letter(c)}: format={fmt}")
        seen: set[str] = set()
        for r, row in enumerate(sheet["data"]):
            for c, cell in enumerate(row):
                addr = f"{_col_letter(c)}{r + 1}"
                formula = sheet["formulas"].get(addr)
                props = sheet["style"].get(addr, {})
                note = sheet.get("comments", {}).get(addr)
                if not cell and not formula and not props and not note:
                    continue
                line = f"{prefix}{addr}: {cell}"
                if formula:
                    line += f"  [{formula}]"
                if note:
                    # A reviewer's note is content in a controlled document — "why is this
                    # figure an exception" is exactly the sort of thing an approver is being
                    # asked to sign off. Without this a note could be added, changed or
                    # deleted between versions and the diff would say "no change".
                    line += f'  <{" ".join(note.split())}>'
                if props:
                    # Formatting is content in a controlled document — a cell going bold, or
                    # a total turning red, is a real change an approver should see in the
                    # diff rather than have it read as "no change".
                    flags = ",".join(f"{k}={v}" if not isinstance(v, bool) else k
                                     for k, v in sorted(props.items()) if v)
                    if flags:
                        line += f"  {{{flags}}}"
                lines.append(line)
                seen.add(addr)
        # A comment can sit on a cell the data grid does not reach — the editor trims trailing
        # blank rows and columns, and a note on an otherwise-empty cell is a legitimate thing
        # to write. Without this the note would be stored and still diff as "no change".
        for addr, note in sorted((sheet.get("comments") or {}).items()):
            if addr not in seen and note:
                lines.append(f'{prefix}{addr}:   <{" ".join(note.split())}>')
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


#: The letterhead repeats on EVERY page, via xhtml2pdf's static frames (P6-S5).
#:
#: It used to be one `<div>` at the top of the body flow and another after the last block, so
#: a three-page policy identified itself on page 1 and pages 2-3 carried nothing — a printed
#: page found on its own was unattributable, which is exactly what a controlled document must
#: not be. `-pdf-frame-content` pulls an element out of the flow and paints it on every page;
#: verified supported at xhtml2pdf/context.py, and the DOCX export has always behaved this way
#: through real Word section headers, so this also closes a PDF/DOCX divergence.
#:
#: THE CONTENT FRAME IS NOT OPTIONAL. Declaring `@frame` boxes turns off xhtml2pdf's implicit
#: single frame, so without `content_frame` the body has nowhere to flow and the render fails —
#: and `_xhtml2pdf` swallows every exception, so the failure presents as the one-line
#: "PDF renderer unavailable" fallback rather than as an error. `test_the_real_pdf_engine_runs`
#: exists to catch precisely that.
#:
#: EVERY GEOMETRY NUMBER HERE WAS MEASURED, by rendering a PDF and reading the text back —
#: none of them is a guess, and none should be nudged without re-running
#: `test_the_letterhead_survives_a_long_organisation_name`. A static frame whose content
#: overflows is not clipped or scaled: it is discarded whole, in silence. Three separate
#: silent losses were found this way during P6-S5 — a 12mm footer swallowing its own text, a
#: 20mm header swallowing itself the moment a logo appeared, and a long organisation name
#: doing the same at 32mm. The frames are oversized on purpose.
_PAGE_CSS = """
@page {
  size: A4;
  @frame header_frame { -pdf-frame-content: doc-header;
                        left: 20mm; top: 10mm; width: 170mm; height: 36mm; }
  @frame content_frame { left: 20mm; top: 50mm; width: 170mm; height: 213mm; }
  @frame footer_frame { -pdf-frame-content: doc-footer;
                        left: 20mm; top: 265mm; width: 170mm; height: 26mm; }
}
body { font-family: 'Helvetica','Arial',sans-serif; font-size: 11pt; color: #1a2432; line-height: 1.5; }
h1 { font-size: 20pt; color: #0E1A2B; margin: 0 0 4pt; }
h2 { font-size: 15pt; color: #0E1A2B; margin: 16pt 0 4pt; }
h3 { font-size: 12.5pt; color: #0E1A2B; margin: 12pt 0 3pt; }
/* A table, not `float:right`. reportlab's float support is partial and the classification
   chip drifted; a two-cell table lands where it is put on every engine. */
.hdr { width: 100%; border-bottom: 2px solid #0E1A2B; }
.hdr td { border: none; padding: 0 0 4pt; vertical-align: bottom; font-size: 10pt; }
.hdr .org { font-weight: bold; font-size: 13pt; color: #0E1A2B; }
.hdr .meta { color: #5B6573; font-size: 8.5pt; padding-top: 2pt; }
.cls { border: 1px solid #5B6573; color: #5B6573; padding: 1pt 5pt;
       font-size: 8pt; letter-spacing: 0.5pt; }
code { background: #eef0f2; padding: 1pt 3pt; border-radius: 2pt; font-family: monospace; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #D5D9DE; padding: 4pt 6pt; text-align: left; font-size: 10pt; }
.foot { border-top: 1px solid #D5D9DE; padding-top: 4pt; color: #9AA1AB; font-size: 8pt; }
.foot td { border: none; padding: 0; font-size: 8pt; color: #9AA1AB; }
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


#: Matches one `<img …>` in nh3's canonical output, capturing its src.
_IMG_TAG = re.compile(r'<img\b[^>]*?\bsrc="([^"]*)"[^>]*>', re.I)

#: How many bytes of decoded image one document may embed. xhtml2pdf holds the whole HTML
#: string AND every decoded image in memory, so this is a real memory figure. An author with
#: forty large photographs gets a degraded PDF; the worker does not fall over.
MAX_EMBEDDED_IMAGE_BYTES = 20 * 1024 * 1024


def _embed_images(html: str, resolver) -> str:
    """Replace every image src with a base64 `data:` URI, or drop the `<img>` entirely.

    **This is what makes the SSRF unreachable rather than merely unlikely.** After this runs,
    no `<img>` in the document has a src that is not `data:`, so xhtml2pdf's
    `FileNetworkManager` can only ever dispatch to `B64InlineURI` — a plain `base64.b64decode`.
    `NetworkFileUri` (which calls `urlopen`) and `LocalFileURI` (which `open()`s any path it is
    given, an arbitrary-file-read primitive) become unreachable by construction.

    `resolver is None` means DROP EVERY IMAGE. The default is never "leave the URL alone" —
    a caller with no way to resolve an image must not hand an unresolved one to the renderer.

    Runs on the sanitised body only, and therefore AFTER `sanitize_document_html`. Running it
    before would mean re-implementing `IMG_SRC_RE` here and maintaining a second security
    boundary that can drift from the first. It re-matches anyway, as an assertion — but an
    assertion is never allowed to be the only check.

    A regex rather than lxml: the input is nh3's own output, where attributes are always
    double-quoted and values always escaped, so the match is exact. Reserialising an HTML
    body through a second parser would also reflow bytes that the next save writes straight
    back to a hashed column.
    """
    budget = MAX_EMBEDDED_IMAGE_BYTES

    def swap(match: re.Match) -> str:
        nonlocal budget
        if resolver is None:
            return ""
        found = IMG_SRC_RE.match(match.group(1))
        if not found:
            return ""
        got = resolver(found.group(1))
        if not got:
            return ""                      # unknown, wrong tenant, or bytes gone from disk
        data, mime = got
        if len(data) > budget:
            return ""
        budget -= len(data)
        uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        return match.group(0).replace(f'src="{match.group(1)}"', f'src="{uri}"', 1)

    return _IMG_TAG.sub(swap, html)


#: The box a logo is scaled into, in xhtml2pdf's image units.
#:
#: Sized against the 36mm header frame WITH ROOM TO SPARE, measured rather than guessed by
#: rendering PDFs and reading them back: at 20mm the header vanished entirely, and a 70-char
#: organisation name still overflowed 32mm. The margin is deliberate, because the failure mode
#: is silent and total — see `_logo_img`.
LOGO_BOX_W, LOGO_BOX_H = 130, 30


def _logo_img(logo_data_uri: str | None, width: int | None, height: int | None) -> str:
    """The letterhead logo, sized so it cannot destroy the header.

    **Both of these were found by rendering a real PDF and reading it back, not by reasoning.**

    1. **The image MUST carry explicit `width`/`height` attributes.** Unsized, xhtml2pdf lays
       it out at its intrinsic pixel size — a 240x120 mark becomes far taller than the 20mm
       header frame — and an overflowing static frame is discarded ENTIRELY, silently. The
       symptom is not a squashed logo; it is a document with no letterhead at all, and no
       error anywhere.
    2. **No `<br/>` after it.** One line break was enough to push the block past the frame and
       produce exactly the same silent loss. The logo now sits beside the organisation name,
       which is where a letterhead usually puts it anyway.

    Falls back to a height-only attribute when the intrinsic size is unknown (Pillow could not
    read it); that also renders, and keeps the aspect ratio.
    """
    if not logo_data_uri:
        return ""
    # No escaping: this string is built by `branding.letterhead` from our own vault bytes,
    # never from anything a user typed. It is base64 of a magic-byte-verified raster.
    if width and height:
        scale = min(LOGO_BOX_W / width, LOGO_BOX_H / height, 1.0)
        size = f' width="{max(1, round(width * scale))}" height="{max(1, round(height * scale))}"'
    else:
        size = f' height="{LOGO_BOX_H}"'
    return f'<img src="{logo_data_uri}"{size} alt="" /> '


def _letterhead_html(*, org: str, title: str, classification: str, version_label: str,
                     status: str, logo_data_uri: str | None,
                     logo_w: int | None = None, logo_h: int | None = None) -> str:
    """The two static-frame blocks. Built AFTER the body is sanitised and never passed through
    the sanitiser — this is our own markup about the tenant, not author input, which is why a
    logo `<img>` here does not touch the `img` ban in `api/html_sanitize.py`.

    The logo arrives as a `data:` URI. xhtml2pdf routes those to `B64InlineURI`, a plain
    `base64.b64decode` — no `urlopen`, so this cannot become the server-side fetch that the
    ban exists to prevent."""
    logo = _logo_img(logo_data_uri, logo_w, logo_h)
    # The header's height must be BOUNDED, not merely usually-small. Everything else in this
    # block is fixed-size; only the organisation name can grow without limit, and a long
    # enough one wraps the block past the frame and silently deletes the whole letterhead.
    # Clamping it here costs nothing real: the footer carries the full legal name, in full, on
    # every single page.
    header_org = org if len(org) <= 60 else org[:57].rstrip() + "\u2026"
    return f"""
<div id="doc-header">
  <table class="hdr"><tr>
    <td>{logo}<span class="org">{_html.escape(header_org)}</span>
        <div class="meta">{_html.escape(title)} &middot; v{version_label}
        &middot; {status}</div></td>
    <td align="right"><span class="cls">{_html.escape(classification)}</span></td>
  </tr></table>
</div>
<div id="doc-footer">
  <table class="foot"><tr>
    <td>{_html.escape(org)} &middot; {_html.escape(title)} v{version_label} &middot;
        Classification: {_html.escape(classification)} &middot; This document is controlled;
        printed copies are uncontrolled.</td>
    <td align="right">Page <pdf:pagenumber> of <pdf:pagecount></td>
  </tr></table>
</div>"""


def build_html(*, title: str, body_md: str, classification: str, version_label: str,
               org: str = DEFAULT_ORG, status: str = "DRAFT",
               content_format: str = "MARKDOWN",
               logo_data_uri: str | None = None, image_resolver=None,
               logo_w: int | None = None, logo_h: int | None = None) -> str:
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
    # Resolve author images to bytes we already hold. Must be AFTER the sanitiser — see
    # `_embed_images`. Applies to the body only, so the letterhead logo below (which is our
    # own markup, not author input) is never rescanned.
    body = _embed_images(body, image_resolver)
    letterhead = _letterhead_html(org=org, title=title, classification=classification,
                                  version_label=version_label, status=status,
                                  logo_data_uri=logo_data_uri, logo_w=logo_w, logo_h=logo_h)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_PAGE_CSS}</style></head><body>
{letterhead}
<h1>{_html.escape(title)}</h1>
{body}
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
               status: str = "DRAFT", content_format: str = "MARKDOWN",
               org: str = DEFAULT_ORG, logo_data_uri: str | None = None,
               image_resolver=None, logo_w: int | None = None,
               logo_h: int | None = None) -> tuple[bytes, str]:
    """Return (pdf_bytes, engine_name).

    `org`/`logo_data_uri` come from `api.branding.letterhead()`. Until P6-S5 this function had
    no `org` parameter at all, so every tenant's export was letterheaded with `DEFAULT_ORG` —
    the first customer's name. Callers that omit them still get that fallback, which is why
    `documents.py` must pass them on every path rather than most of them."""
    html = build_html(title=title, body_md=body_md, classification=classification,
                      version_label=version_label, status=status,
                      content_format=content_format, org=org, logo_data_uri=logo_data_uri,
                      image_resolver=image_resolver, logo_w=logo_w, logo_h=logo_h)
    for name, fn in (("weasyprint", _weasyprint), ("xhtml2pdf", _xhtml2pdf)):
        data = fn(html)
        if data and data[:4] == b"%PDF":
            return data, name
    return _minimal_pdf(title), "minimal"
