"""P5-S2 / S2b — SHEET content: parsing, validation, and the JSON -> HTML conversion that
both the PDF and DOCX export paths route through.
"""

import io
import json
import re

import pytest

from api.rendering.render import (DEFAULT_ORG, SheetFormatError, build_html, parse_sheet, render_pdf,
                        sheet_json_to_html)

V1 = json.dumps({"data": [["Name", "Score"], ["Alice", 9], [None, "n/a"]],
                 "bold": ["A1", "B1"], "align": {"B1": "center"}})


def _sheet(content):
    """The single worksheet of a parsed one-sheet document."""
    return parse_sheet(content)["sheets"][0]


# ────────────────────────────────────────────── v1 (P5-S2) — permanent compatibility

def test_v1_is_upcast_to_the_v2_structure():
    book = parse_sheet(V1)
    assert book["version"] == 1
    s = book["sheets"][0]
    assert s["data"] == [["Name", "Score"], ["Alice", "9"], ["", "n/a"]]
    assert s["style"]["A1"] == {"bold": True}
    assert s["style"]["B1"] == {"bold": True, "align": "center"}


def test_v1_renders_byte_for_byte_as_it_always_did():
    """A published v1 sheet can NEVER be migrated — freeze_published_version() makes
    `content` immutable and content_sha256 (which backs electronic_signatures) is generated
    from it. So the v1 reader is permanent, and its output must not drift."""
    html = sheet_json_to_html(json.dumps({
        "data": [["Control", "Status"], ["MFA", "Done"]],
        "bold": ["A1", "B1"], "align": {"B2": "right"}}))
    assert html == (
        "<table><tr><td><strong>Control</strong></td><td><strong>Status</strong></td></tr>"
        '<tr><td>MFA</td><td style="text-align:right">Done</td></tr></table>')


def test_explicit_version_1_is_also_accepted():
    assert parse_sheet(json.dumps({"version": 1, "data": [["x"]]}))["sheets"][0]["data"] == [["x"]]


# ────────────────────────────────────────────── empty / malformed

def test_empty_content_is_a_new_sheet_not_an_error():
    for blank in ("", "{}"):
        book = parse_sheet(blank)
        assert book["sheets"][0]["data"] == []
    assert sheet_json_to_html("") == "<table></table>"


def test_a_typo_is_rejected_rather_than_silently_rendering_an_empty_grid():
    """Neither a version discriminator nor any v1 key — that is a bug in the caller, and
    quietly returning a blank sheet would hide it."""
    with pytest.raises(SheetFormatError, match="missing 'version'"):
        parse_sheet(json.dumps({"dta": [["typo"]]}))


@pytest.mark.parametrize("content", [
    "not json at all",
    "[]",                                            # not a JSON object
    '{"data": "not a list"}',
    '{"data": [["ok"], "not a row"]}',
    '{"data": [[{"nested": "object"}]]}',            # a cell must be text/number/null
    '{"bold": "A1"}',
    '{"align": {"A1": "diagonal"}}',
    '{"version": 3, "sheets": []}',                  # unknown version
    '{"version": 2, "sheets": []}',                  # must be non-empty
    '{"version": 2, "sheets": "nope"}',
])
def test_malformed_content_is_rejected(content):
    with pytest.raises(SheetFormatError):
        parse_sheet(content)


# ────────────────────────────────────────────── v2 (P5-S2b)

def _v2(**sheet):
    return json.dumps({"version": 2, "sheets": [{"name": "S", **sheet}]})


def test_v2_round_trips_values_styles_merges_and_widths():
    s = _sheet(_v2(
        data=[["Item", "Cost"], ["Laptop", 1200]],
        style={"A1": {"bold": True, "align": "center", "fontSize": 12,
                      "color": "#1a2432", "background": "#eef0f2"},
               "A2": {"italic": True, "underline": True}},
        merges={"A1": {"colspan": 2, "rowspan": 1}},
        colWidths=[140, 90]))
    assert s["name"] == "S"
    assert s["data"] == [["Item", "Cost"], ["Laptop", "1200"]]
    assert s["style"]["A1"]["fontSize"] == 12
    assert s["merges"]["A1"] == {"colspan": 2, "rowspan": 1}
    assert s["colWidths"] == [140.0, 90.0]


def test_formula_source_is_kept_alongside_the_computed_value():
    """`data` holds what the browser computed; `formulas` holds the source. Python has no
    formula engine, so the computed value is what the PDF/DOCX render — and freezing it at
    publish is exactly why a signed sheet shows what its approvers signed."""
    s = _sheet(_v2(data=[["1", "2", "3"]], formulas={"C1": "=SUM(A1:B1)"}))
    assert s["data"][0][2] == "3"          # the value renders
    assert s["formulas"]["C1"] == "=SUM(A1:B1)"   # the source survives for the editor


@pytest.mark.parametrize("bad", [
    {"formulas": {"C1": "SUM(A1:B1)"}},              # missing leading '='
    {"formulas": {"nope": "=1"}},                    # not a cell address
    {"formulas": {"C1": 5}},                         # not a string
    {"style": {"A1": {"align": "diagonal"}}},
    {"style": {"A1": {"fontSize": 900}}},            # out of bounds
    {"style": {"A1": {"fontSize": "12"}}},           # wrong type
    {"style": {"A1": {"color": "red"}}},             # hex only
    {"style": {"A1": {"bold": "yes"}}},              # wrong type
    {"style": {"ZZZZ9": {"bold": True}}},            # not a cell address
    {"merges": {"A1": {"colspan": 0}}},
    {"merges": {"A1": {"colspan": 99999}}},
    {"colWidths": [-5]},
    {"colWidths": ["wide"]},
])
def test_v2_validation_rejects_bad_values(bad):
    with pytest.raises(SheetFormatError):
        parse_sheet(_v2(data=[["x"]], **bad))


def test_unknown_style_property_is_refused_and_names_what_is_allowed():
    """The style allow-list IS the injection boundary — these values land in a style=""
    attribute that is later rendered to PDF. A passthrough would be raw CSS injection."""
    with pytest.raises(SheetFormatError, match="unsupported style 'position'"):
        parse_sheet(_v2(data=[["x"]], style={"A1": {"position": "absolute"}}))


def test_v2_html_carries_styles_merges_and_marks():
    html = sheet_json_to_html(_v2(
        data=[["Item", "Cost"], ["Laptop", "1200"]],
        style={"A1": {"bold": True, "align": "center", "background": "#eef0f2"},
               "B2": {"italic": True, "underline": True, "fontSize": 12, "color": "#1a2432"}},
        merges={"A1": {"colspan": 2, "rowspan": 1}}))
    assert 'colspan="2"' in html
    assert "background-color:#eef0f2" in html
    assert "font-size:12pt" in html and "color:#1a2432" in html
    assert "<strong>Item</strong>" in html
    assert "<em>" in html and "<u>" in html


def test_bold_never_becomes_th():
    """<th> would be a semantic lie (bold can be anywhere) AND would make docx_export treat
    an all-bold first row as a page-repeating header."""
    html = sheet_json_to_html(_v2(data=[["A", "B"]],
                                  style={"A1": {"bold": True}, "B1": {"bold": True}}))
    assert "<th" not in html
    assert html.count("<strong>") == 2


def test_cell_text_is_escaped():
    html = sheet_json_to_html(_v2(data=[["<script>alert(1)</script>"]]))
    assert "<script>" not in html and "&lt;script&gt;" in html


# ────────────────────────────────────────────── multiple worksheets

MULTI = json.dumps({"version": 2, "sheets": [
    {"name": "Risks", "data": [["R1"]]},
    {"name": "Assets", "data": [["A1"]]}]})


def test_multi_sheet_titles_each_worksheet_and_page_breaks_between():
    html = sheet_json_to_html(MULTI)
    assert "<h2>Risks</h2>" in html
    assert '<h2 style="page-break-before:always">Assets</h2>' in html
    assert html.count("<table>") == 2


def test_a_single_sheet_gets_no_heading():
    """So a one-sheet document — every v1 doc, and the common case — renders exactly as it
    did before multi-sheet support existed."""
    assert "<h2" not in sheet_json_to_html(_v2(data=[["x"]]))


def test_sheet_name_is_escaped():
    html = sheet_json_to_html(json.dumps({"version": 2, "sheets": [
        {"name": "<img src=x>", "data": [["a"]]}, {"name": "two", "data": [["b"]]}]}))
    assert "<img" not in html and "&lt;img" in html


# ────────────────────────────────────────────── through the full PDF pipeline

def test_build_html_keeps_sheet_styling_through_the_sanitiser():
    """The sanitiser filters style declarations to an allow-list. If font-size/color/
    background-color are not on it, a formatted cell renders in the editor and silently
    loses its formatting in the PDF — invisible data loss."""
    html = build_html(title="Asset Register", classification="INTERNAL", version_label="1.0",
                      content_format="SHEET", body_md=_v2(
                          data=[["Asset", "Owner"], ["Laptop-1", "Alice"]],
                          style={"A1": {"bold": True, "align": "center", "fontSize": 12,
                                        "color": "#1a2432", "background": "#eef0f2"}},
                          merges={"A1": {"colspan": 2, "rowspan": 1}}))
    assert "<strong>Asset</strong>" in html
    assert "text-align:center" in html
    assert "font-size:12pt" in html
    assert "color:#1a2432" in html
    assert "background-color:#eef0f2" in html
    assert 'colspan="2"' in html
    assert "Laptop-1" in html and "Alice" in html


# ────────────────────────────────────────────── P5-S2c: per-column wrap

def test_colwrap_round_trips_and_rejects_non_booleans():
    s = _sheet(_v2(data=[["x", "y"]], colWrap=[True, False]))
    assert s["colWrap"] == [True, False]
    with pytest.raises(SheetFormatError, match="colWrap"):
        parse_sheet(_v2(data=[["x"]], colWrap=["yes"]))


def test_a_wrapped_column_gets_pre_wrap_and_others_are_untouched():
    html = sheet_json_to_html(_v2(data=[["long text", "short"]], colWrap=[True, False]))
    cells = html.split("<td")
    assert "white-space:pre-wrap" in cells[1]        # column A wraps
    assert "white-space" not in cells[2]             # column B is left exactly as before


def test_wrap_is_purely_additive_for_documents_that_never_used_it():
    """A document with no colWrap must render byte-for-byte as it did before the feature
    existed — published versions are frozen and hashed, so their output must not drift."""
    body = _v2(data=[["a", "b"]], style={"A1": {"bold": True}})
    assert sheet_json_to_html(body) == sheet_json_to_html(json.dumps({
        "version": 2, "sheets": [{"name": "S", "data": [["a", "b"]],
                                  "style": {"A1": {"bold": True}}}]}))
    assert "white-space" not in sheet_json_to_html(body)
    # …and a v1 document, which can never carry colWrap at all
    assert "white-space" not in sheet_json_to_html(V1)


def test_wrap_survives_the_sanitiser_into_the_pdf():
    """white-space must be on FILTER_STYLE_PROPERTIES or a wrapped column renders wrapped in
    the editor and unwrapped in the PDF — invisible divergence."""
    html = build_html(title="T", classification="INTERNAL", version_label="1.0",
                      content_format="SHEET",
                      body_md=_v2(data=[["long text"]], colWrap=[True]))
    assert "white-space:pre-wrap" in html


# ────────────────────────────────────────────── P6-S4: per-column number formats

def test_colformat_round_trips_and_rejects_anything_off_the_allow_list():
    s = _sheet(_v2(data=[["x", "y", "z"]], colFormat=["currency:INR", "percent", ""]))
    assert s["colFormat"] == ["currency:INR", "percent", ""]
    for bad in (["currency:XYZ"], ["currency"], ["dollars"], [True], ["CURRENCY:INR"]):
        with pytest.raises(SheetFormatError, match="colFormat"):
            parse_sheet(_v2(data=[["x"]], colFormat=bad))


def test_currency_and_percent_render_the_way_excel_spells_them():
    html = sheet_json_to_html(_v2(
        data=[["1234.5", "0.15"], ["-5", "1"]],
        colFormat=["currency:INR", "percent"]))
    assert "₹1,234.50" in html
    # sign OUTSIDE the symbol — the accounting convention, and what formatCell produces
    assert "-₹5.00" in html
    # percent reads the stored number as a RATIO, because the .xlsx `0.00%` format does
    assert "15.00%" in html and "100.00%" in html


def test_a_formatted_column_leaves_its_header_alone():
    """The whole reason `format_cell_value` exists rather than an f-string at the call site,
    and the reason the editor cannot use jspreadsheet's own `mask` option: a register's
    header sits in the same column as its figures, and jsuites' mask renders the text
    "Annual cost" as a bare "₹" — silently destroying the top row of every register."""
    html = sheet_json_to_html(_v2(
        data=[["Annual cost"], ["1200"], [""], ["n/a"]], colFormat=["currency:USD"]))
    assert "<td>Annual cost</td>" in html
    assert "<td>$1,200.00</td>" in html
    assert "<td></td>" in html            # blank stays blank, not "$0.00"
    assert "<td>n/a</td>" in html


def test_formatting_is_display_only_and_the_stored_number_is_untouched():
    """The point of storing the format beside the value rather than baking it in: the same
    cell can be re-read in another currency, or none, because the number was never lost."""
    body = _v2(data=[["1234.5"]], colFormat=["currency:INR"])
    assert _sheet(body)["data"] == [["1234.5"]]
    assert "$1,234.50" in sheet_json_to_html(_v2(data=[["1234.5"]], colFormat=["currency:USD"]))
    assert "<td>1234.5</td>" in sheet_json_to_html(_v2(data=[["1234.5"]]))


def test_number_format_is_purely_additive_for_documents_that_never_used_it():
    """Published versions are frozen and hashed, so output for a document with no colFormat
    must not drift by a single byte."""
    body = _v2(data=[["1200", "0.5"]], style={"A1": {"bold": True}})
    assert sheet_json_to_html(body) == sheet_json_to_html(json.dumps({
        "version": 2, "sheets": [{"name": "S", "data": [["1200", "0.5"]],
                                  "style": {"A1": {"bold": True}}}]}))
    assert "₹" not in sheet_json_to_html(body) and "%" not in sheet_json_to_html(body)
    assert "%" not in sheet_json_to_html(V1)


def test_only_real_numbers_are_formatted_so_the_grid_and_the_pdf_agree():
    """`float()` accepts "1_000", "nan" and "inf"; JavaScript's `Number()` does not. If this
    used float() the PDF would format cells the browser grid had left alone — the editor and
    the approved document would show different text for the same cell."""
    html = sheet_json_to_html(_v2(
        data=[["1_000"], ["nan"], ["inf"], [" 12"], ["0x1f"], ["1e3"]],
        colFormat=["currency:USD"]))
    for untouched in ("1_000", "nan", "inf", " 12", "0x1f"):
        assert f"<td>{untouched}</td>" in html
    assert "<td>$1,000.00</td>" in html          # 1e3 IS a number to both sides


def test_a_column_format_change_is_visible_in_the_version_diff():
    """A currency->percent flip changes every figure an approver reads while leaving `data`
    byte-identical, so without a diff line it would present as "no change"."""
    from api.rendering.render import sheet_diff_lines

    before = sheet_diff_lines(_v2(data=[["1200"]], colFormat=["currency:INR"]))
    after = sheet_diff_lines(_v2(data=[["1200"]], colFormat=["percent"]))
    assert "column A: format=currency:INR" in before
    assert "column A: format=percent" in after
    assert before != after
    # …and a document that uses no formats gains no lines at all
    assert not [ln for ln in sheet_diff_lines(_v2(data=[["1200"]])) if "format=" in ln]


def test_formatted_cells_survive_the_sanitiser_into_the_pdf():
    html = build_html(title="Vendor Register", classification="INTERNAL", version_label="1.0",
                      content_format="SHEET",
                      body_md=_v2(data=[["Annual cost", "Uptime"], ["1234.5", "0.999"]],
                                  colFormat=["currency:INR", "percent"]))
    assert "₹1,234.50" in html and "99.90%" in html
    assert "Annual cost" in html and "Uptime" in html


# ────────────────────────────────────────────── P6-S5: the tenant letterhead

#: A one-pixel PNG, small enough to inline and real enough for Pillow and reportlab to accept.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63e095d20600008a005328b499f20000000049454e44"
    "ae426082")


def _pdf_pages(pdf: bytes) -> list[str]:
    """Every page's text, whitespace-flattened. `pypdf` is a hard dependency of xhtml2pdf
    (see its METADATA), so it is present wherever the renderer is."""
    from pypdf import PdfReader

    return [re.sub(r"\s+", " ", page.extract_text() or "")
            for page in PdfReader(io.BytesIO(pdf)).pages]


def _long_policy(n: int = 60) -> str:
    return "".join(f"<p>Clause {i} of a policy long enough to run over several pages.</p>"
                   for i in range(n))


def test_the_real_pdf_engine_runs():
    """THE canary for this whole change, and it earns its place: `_xhtml2pdf` swallows every
    exception and `render_pdf` then falls through to `_minimal_pdf`, which returns a valid
    one-line PDF reading "PDF renderer unavailable". Every existing assertion — `%PDF`, a
    200, a content-type — passes on that stub. So a broken `@page` frame would ship as a
    published policy containing one sentence, and nothing would fail.

    This bit, once: a 12mm footer frame silently swallowed its own contents during
    development. Static frames drop overflow without an error."""
    pdf, engine = render_pdf(title="T", body_md="<p>x</p>", classification="INTERNAL",
                             version_label="1.0", content_format="HTML")
    assert engine == "xhtml2pdf", "the real renderer must run, not the placeholder fallback"
    assert pdf[:4] == b"%PDF"


def test_the_letterhead_repeats_on_every_page():
    """It used to be one block at the top of the body flow, so page 1 identified the document
    and pages 2-3 carried nothing — a printed page found on its own was unattributable, which
    is the one thing a controlled document must never be. The DOCX has always repeated its
    header through real Word section headers, so this also closes that divergence."""
    pdf, _ = render_pdf(title="Access Control Policy", body_md=_long_policy(),
                        classification="CONFIDENTIAL", version_label="2.1",
                        status="PUBLISHED", content_format="HTML",
                        org="Northwind Manufacturing Pvt Ltd")
    pages = _pdf_pages(pdf)
    assert len(pages) > 1, "the fixture must span pages or this proves nothing"
    for i, text in enumerate(pages, start=1):
        assert "Northwind Manufacturing Pvt Ltd" in text, f"no letterhead on page {i}"
        assert "CONFIDENTIAL" in text, f"no classification on page {i}"
        assert "printed copies are uncontrolled" in text, f"no footer on page {i}"


def test_the_page_count_is_real():
    """P6-S1 refused to print "Page 1 of 3" because there was no pagination and "a page count
    that isn't real is the same defect class as the search box that didn't search". Static
    frames give xhtml2pdf's `<pdf:pagecount>` something true to say, so it can go in now."""
    pdf, _ = render_pdf(title="T", body_md=_long_policy(), classification="INTERNAL",
                        version_label="1.0", content_format="HTML")
    pages = _pdf_pages(pdf)
    total = len(pages)
    for i, text in enumerate(pages, start=1):
        assert f"Page {i} of {total}" in text, f"page {i} says something other than {i}/{total}"


def test_the_tenant_name_replaces_the_hardcoded_default():
    """The defect this fixes: `render_pdf` had no `org` parameter at all, so every customer's
    signed policy was letterheaded with the first customer's company name."""
    html = build_html(title="T", body_md="<p>x</p>", classification="INTERNAL",
                      version_label="1.0", content_format="HTML", org="Acme Pvt Ltd")
    assert "Acme Pvt Ltd" in html
    assert DEFAULT_ORG not in html


def test_the_logo_is_embedded_and_can_never_be_fetched():
    """The letterhead is built AFTER `sanitize_document_html(body)`, so its `<img>` is not
    subject to the sanitiser's `img` ban — correctly, because it is our own markup about the
    tenant rather than author input. What makes that safe is the form: a `data:` URI reaches
    xhtml2pdf's `B64InlineURI`, a plain base64 decode. `NetworkFileUri`, the class that calls
    `urlopen`, is only reachable from an `http`/`https` src, and there are none."""
    import base64

    uri = f"data:image/png;base64,{base64.b64encode(PNG_1PX).decode()}"
    html = build_html(title="T", body_md="<p>x</p>", classification="INTERNAL",
                      version_label="1.0", content_format="HTML", logo_data_uri=uri)
    srcs = re.findall(r'<img[^>]*\bsrc="([^"]*)"', html)
    assert srcs and all(s.startswith("data:image/") for s in srcs)
    assert not re.search(r'src="https?:', html)

    pdf, engine = render_pdf(title="T", body_md="<p>x</p>", classification="INTERNAL",
                             version_label="1.0", content_format="HTML", logo_data_uri=uri)
    assert engine == "xhtml2pdf" and pdf[:4] == b"%PDF"


@pytest.mark.parametrize("size", [(240, 120), (1200, 400), (40, 40), (None, None)])
def test_a_logo_never_costs_the_document_its_letterhead(size):
    """The bug this exists for, found by rendering a PDF and reading it back rather than by
    reasoning about it: adding a logo made the ENTIRE header frame disappear.

    A static `@frame` whose content overflows is discarded silently by xhtml2pdf — not
    clipped, not scaled, dropped. An `<img>` with no width/height attributes lays out at its
    intrinsic pixel size, so a perfectly ordinary 240x120 mark overflowed a 20mm header and
    took the organisation name, the classification and the title with it. A single `<br/>`
    after the logo did the same thing on its own.

    Nothing about the earlier assertions caught it: the HTML contained the logo, the engine
    was still xhtml2pdf, and the PDF was still a valid PDF. Only reading the rendered page
    back shows a document with no letterhead at all — so that is what this asserts, across
    the aspect ratios a real customer logo might have."""
    import base64

    w, h = size
    uri = f"data:image/png;base64,{base64.b64encode(_png(w or 240, h or 120)).decode()}"
    pdf, engine = render_pdf(title="Network Security Policy", body_md=_long_policy(20),
                             classification="INTERNAL", version_label="1.0", status="DRAFT",
                             content_format="HTML", org="Northwind Manufacturing Pvt Ltd",
                             logo_data_uri=uri, logo_w=w, logo_h=h)
    assert engine == "xhtml2pdf"
    pages = _pdf_pages(pdf)
    for i, text in enumerate(pages, start=1):
        assert "Northwind Manufacturing Pvt Ltd" in text, f"no letterhead on page {i}"
        assert "DRAFT" in text, f"the header block vanished on page {i}"
        assert "INTERNAL" in text, f"no classification on page {i}"
    assert pdf.count(b"/Subtype /Image") >= 1, "the logo was not embedded"


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (13, 26, 43)).save(buf, format="PNG")
    return buf.getvalue()


def test_a_document_body_still_cannot_carry_an_image():
    """The letterhead exemption must not leak into author content. Until the image store
    lands, an `<img>` in the body is still stripped by the sanitiser."""
    html = build_html(title="T", classification="INTERNAL", version_label="1.0",
                      content_format="HTML",
                      body_md='<p><img src="https://evil.test/x.png"></p>')
    assert "evil.test" not in html


LONG_ORG = ("Northwind Manufacturing and Allied Industrial Services Private Limited, "
            "Regional Division for Southern Operations and Logistics Holdings")


@pytest.mark.parametrize("org", ["Acme Ltd", LONG_ORG[:70], LONG_ORG])
@pytest.mark.parametrize("logo", [None, (240, 120), (2000, 200), (200, 2000), (16, 16)])
def test_the_letterhead_survives_a_long_organisation_name(org, logo):
    """The frame geometry in `_PAGE_CSS` is measured, not guessed, and this is what measured
    it. A static frame whose content overflows is discarded WHOLE and in SILENCE — so a long
    company name, or a logo of an unexpected shape, could delete the letterhead from every
    page of a signed policy without any error being raised or any other assertion failing.

    Header height is bounded by construction (the organisation name is clamped for the header
    block only — the footer still carries it in full), and both frames are deliberately
    oversized. If you shrink either, this test is what tells you.
    """
    import base64

    kwargs = {}
    if logo:
        kwargs = {"logo_data_uri":
                  f"data:image/png;base64,{base64.b64encode(_png(*logo)).decode()}",
                  "logo_w": logo[0], "logo_h": logo[1]}
    pdf, engine = render_pdf(
        title="Network Security Policy for Perimeter Devices", body_md=_long_policy(40),
        classification="CONFIDENTIAL", version_label="10.12", status="PUBLISHED",
        content_format="HTML", org=org, **kwargs)

    assert engine == "xhtml2pdf"
    for i, text in enumerate(_pdf_pages(pdf), start=1):
        # "PUBLISHED" appears ONLY in the header block; the footer never carries the status,
        # so it is the one marker that cannot be satisfied by the other frame. Asserting on
        # the org name alone would have passed with the header entirely gone — it did.
        assert "PUBLISHED" in text, f"the header frame was dropped on page {i}"
        assert "CONFIDENTIAL" in text, f"no classification on page {i}"
        assert "uncontrolled" in text, f"the footer frame was dropped on page {i}"
        assert re.search(r"Page \d+ of \d+", text), f"no page number on page {i}"


# ────────────────────────────────────────────── P6-S5b: cell comments

def test_comments_round_trip_and_are_validated():
    s = _sheet(_v2(data=[["1200"]], comments={"A1": "Board approved this exception"}))
    assert s["comments"] == {"A1": "Board approved this exception"}

    for bad in ({"nope": "x"}, {"A1": 5}, {"A1": "x" * 3000}):
        with pytest.raises(SheetFormatError):
            parse_sheet(_v2(data=[["x"]], comments=bad))


def test_an_empty_comment_is_dropped_rather_than_stored():
    """jspreadsheet clears a comment by setting it to "", so the empty string arrives here on
    every deletion. Storing it would leave an uncommented sheet's JSON permanently different
    from one that never had a comment."""
    assert _sheet(_v2(data=[["x"]], comments={"A1": ""}))["comments"] == {}


def test_a_comment_shows_up_in_the_version_diff():
    """A reviewer's note is content: "why is this figure an exception" is exactly what an
    approver is being asked to sign off. Without this, a note could be added, reworded or
    deleted between versions and the diff would report no change at all."""
    from api.rendering.render import sheet_diff_lines

    before = sheet_diff_lines(_v2(data=[["1200"]]))
    after = sheet_diff_lines(_v2(data=[["1200"]], comments={"A1": "Board approved"}))
    assert before != after
    assert any("Board approved" in ln for ln in after)

    reworded = sheet_diff_lines(_v2(data=[["1200"]], comments={"A1": "Board rejected"}))
    assert reworded != after


def test_a_comment_on_an_empty_cell_still_reaches_the_diff():
    """The editor trims trailing blank rows and columns, so a note on an otherwise-empty cell
    sits outside `data` entirely — and would be stored but invisible to an approver."""
    from api.rendering.render import sheet_diff_lines

    lines = sheet_diff_lines(_v2(data=[["x"]], comments={"D9": "chase the vendor"}))
    assert any("D9" in ln and "chase the vendor" in ln for ln in lines)


def test_comments_do_not_print_into_the_pdf_body():
    """Matching Excel: a comment is a note about the work, not a row of the register. It
    reaches the .xlsx as a real Excel comment and the read view as a marker; putting every
    note inline would wreck the table it annotates."""
    html = sheet_json_to_html(_v2(data=[["1200"]], comments={"A1": "Board approved"}))
    assert "Board approved" not in html


def test_comments_are_purely_additive_for_documents_that_never_used_them():
    body = _v2(data=[["a", "b"]], style={"A1": {"bold": True}})
    assert sheet_json_to_html(body) == sheet_json_to_html(json.dumps({
        "version": 2, "sheets": [{"name": "S", "data": [["a", "b"]],
                                  "style": {"A1": {"bold": True}}}]}))
