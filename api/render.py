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


def parse_sheet(content: str) -> dict:
    """Parse and validate a SHEET version's stored JSON.

    Shape: `{"data": [[cell, ...], ...], "bold": ["A1", ...], "align": {"A1": "center"}}`.

    `data` is a grid of plain-text cell values — P5-S2's stated scope is "values and basic
    cell formatting only, no formulas": a formula engine that disagrees with Excel's is
    worse than no formula engine in a compliance record. `bold` and `align` are the two
    pieces of cell-level formatting supported, both by A1-style address — not arbitrary
    per-cell CSS, which would be a needless stored-content injection surface for a document
    type that's meant to hold a grid of numbers, not rich styling. Both map onto mechanisms
    `docx_export.py` already has (`_MARK_TAGS["strong"]`, `_ALIGN`), so exporting them costs
    no new Word-writing code, only reading these two attributes where the walker already
    reads inline style for paragraphs.

    The jspreadsheet-ce editor's own toolbar offers more than this (italic, colour, …); only
    bold and alignment survive a save — the editor UI says so, so this is not a silent loss.

    Raises `SheetFormatError` with a message safe to surface in a 400, never lets a
    malformed grid reach json.JSONDecodeError or a raw KeyError/TypeError in a caller.
    """
    try:
        obj = json.loads(content or "{}")
    except json.JSONDecodeError as e:
        raise SheetFormatError(f"not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise SheetFormatError("must be a JSON object")
    data = obj.get("data", [])
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        raise SheetFormatError("'data' must be a list of rows")
    for row in data:
        if not all(isinstance(cell, (str, int, float)) or cell is None for cell in row):
            raise SheetFormatError("every cell must be text, a number, or null")
    bold = obj.get("bold", [])
    if not isinstance(bold, list) or not all(isinstance(a, str) for a in bold):
        raise SheetFormatError("'bold' must be a list of cell addresses")
    align = obj.get("align", {})
    if not isinstance(align, dict) or not all(
            isinstance(k, str) and v in ALIGNMENTS for k, v in align.items()):
        raise SheetFormatError(f"'align' values must be one of {', '.join(ALIGNMENTS)}")
    return {"data": [[("" if c is None else str(c)) for c in row] for row in data],
            "bold": set(bold), "align": dict(align)}


def _col_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA — spreadsheet column addressing."""
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def sheet_json_to_html(content: str) -> str:
    """The one place a SHEET's stored JSON becomes markup. Both the PDF and DOCX export
    paths route through this, then through their EXISTING html-table handling — `build_html`
    already sanitises whatever HTML it's given, and `docx_export._Walker._table()` already
    turns an HTML `<table>` into a Word table — so a spreadsheet needs no export code of its
    own beyond this one conversion."""
    sheet = parse_sheet(content)
    rows_html = []
    for r, row in enumerate(sheet["data"]):
        cells = []
        for c, cell in enumerate(row):
            addr = f"{_col_letter(c)}{r + 1}"
            text = _html.escape(cell)
            if addr in sheet["bold"]:
                # <strong>, not <th> — bold here means "this cell's text is bold," not
                # "this is a header cell." <th> would be a semantic mismatch (bold can be
                # anywhere in the grid, not just a header row) and would also make
                # docx_export._table() treat an all-bold first row as a page-repeating
                # header, which isn't what this means.
                text = f"<strong>{text}</strong>"
            align = sheet["align"].get(addr)
            style = f' style="text-align:{align}"' if align else ""
            cells.append(f"<td{style}>{text}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows_html)}</table>" if rows_html else "<table></table>"


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
