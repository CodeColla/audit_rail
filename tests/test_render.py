"""P5-S2 / S2b — SHEET content: parsing, validation, and the JSON -> HTML conversion that
both the PDF and DOCX export paths route through.
"""

import json

import pytest

from api.render import SheetFormatError, build_html, parse_sheet, sheet_json_to_html

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
