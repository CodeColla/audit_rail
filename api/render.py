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
    body = (body_md or "") if content_format == "HTML" else md_to_html(body_md)
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
