"""P5-S2 — SHEET content: parsing/validation and the JSON -> HTML conversion both the PDF
and DOCX export paths route through.
"""

import json

import pytest

from api.render import SheetFormatError, build_html, parse_sheet, sheet_json_to_html


def test_parse_sheet_accepts_a_valid_grid():
    sheet = parse_sheet(json.dumps({
        "data": [["Name", "Score"], ["Alice", 9], [None, "n/a"]],
        "bold": ["A1", "B1"], "align": {"B1": "center"}}))
    assert sheet["data"] == [["Name", "Score"], ["Alice", "9"], ["", "n/a"]]
    assert sheet["bold"] == {"A1", "B1"}
    assert sheet["align"] == {"B1": "center"}


def test_parse_sheet_defaults_to_an_empty_grid():
    assert parse_sheet("") == {"data": [], "bold": set(), "align": {}}
    assert parse_sheet("{}") == {"data": [], "bold": set(), "align": {}}


@pytest.mark.parametrize("content", [
    "not json at all",
    "[]",                                          # not a JSON object
    '{"data": "not a list"}',
    '{"data": [["ok"], "not a row"]}',
    '{"data": [[{"nested": "object"}]]}',           # a cell must be text/number/null
    '{"bold": "A1"}',                               # bold must be a list
    '{"bold": [1]}',                                # bold entries must be strings
    '{"align": {"A1": "diagonal"}}',                # not one of left/center/right
])
def test_parse_sheet_rejects_malformed_content(content):
    with pytest.raises(SheetFormatError):
        parse_sheet(content)


def test_sheet_json_to_html_renders_a_table_with_bold_and_alignment():
    html = sheet_json_to_html(json.dumps({
        "data": [["Control", "Status"], ["MFA", "Done"]],
        "bold": ["A1", "B1"], "align": {"B2": "right"}}))
    assert html == (
        "<table><tr><td><strong>Control</strong></td><td><strong>Status</strong></td></tr>"
        '<tr><td>MFA</td><td style="text-align:right">Done</td></tr></table>')


def test_sheet_json_to_html_never_emits_th():
    """An all-bold row must render as bold <td>s, not <th> — <th> would falsely trigger
    docx_export's page-repeating-header logic for a first row that just happens to be bold."""
    html = sheet_json_to_html(json.dumps({"data": [["A", "B"]], "bold": ["A1", "B1"]}))
    assert "<th" not in html
    assert html.count("<strong>") == 2


def test_sheet_json_to_html_escapes_cell_text():
    html = sheet_json_to_html(json.dumps({"data": [["<script>alert(1)</script>"]]}))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_sheet_json_to_html_empty_grid():
    assert sheet_json_to_html("{}") == "<table></table>"


def test_build_html_dispatches_sheet_format():
    html = build_html(title="Asset Register", body_md=json.dumps({
        "data": [["Asset", "Owner"], ["Laptop-1", "Alice"]], "bold": ["A1", "B1"]}),
        classification="INTERNAL", version_label="1.0", content_format="SHEET")
    assert "<table>" in html
    assert "Laptop-1" in html and "Alice" in html
    assert "<strong>Asset</strong>" in html
