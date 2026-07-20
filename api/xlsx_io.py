"""XLSX import parsing and export building (M6), via openpyxl.

Import: generalizes the Phase-0 stdlib parser — detect a header row and the
question / number / section columns (or take them as hints), then yield rows.
Export: build a clean answers workbook from an assessment's responses.
"""

from __future__ import annotations

import io
import re

from openpyxl import Workbook, load_workbook

_QUESTION_HINTS = ("question", "control", "requirement", "particular", "checkpoint")
_NUMBER_HINTS = ("s.no", "sr.no", "sno", "srno", "s no", "no.", "ref", "#", "control no")
_SECTION_HINTS = ("domain", "section", "category", "area", "control area", "sub domain")


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower()) if v is not None else ""


def _header_role(val: str):
    """(role, strength) for a header cell. 'question' with strength 2 = a column
    literally named 'question' (beats a weak 'control'/'requirement' match, and
    keeps 'Control Domain' as a section rather than the question column)."""
    if "question" in val:
        return "question", 2
    if any(h in val for h in _SECTION_HINTS):
        return "section", 0
    if any(val == h or h in val for h in _NUMBER_HINTS):
        return "number", 0
    if any(h in val for h in _QUESTION_HINTS):  # control/requirement/... = weak question
        return "question", 1
    return None, 0


def _find_header(ws, scan_rows: int = 12):
    """Return (header_row_idx, {role: col_idx}) by scanning the first rows."""
    for r in range(1, min(scan_rows, ws.max_row or 1) + 1):
        cells = [_norm(c.value) for c in ws[r]]
        joined = " | ".join(cells)
        if not any(h in joined for h in _QUESTION_HINTS):
            continue
        q_best, q_strength = None, -1
        cols: dict = {}
        for i, val in enumerate(cells):
            if not val:
                continue
            role, strength = _header_role(val)
            if role == "question":
                if strength > q_strength:
                    q_best, q_strength = i, strength
            elif role and role not in cols:
                cols[role] = i
        if q_best is not None:
            cols["question"] = q_best
            return r, cols
    return None, {}


def parse_checklist(data: bytes, sheet: str | None = None,
                    question_col: int | None = None, number_col: int | None = None,
                    section_col: int | None = None, header_row: int | None = None):
    """Return (meta, rows). rows = [{number, section, text}] with section forward-filled."""
    # not read_only: in read_only mode ws.max_row can be None for some files
    wb = load_workbook(io.BytesIO(data), read_only=False, data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active

    if question_col is None:
        header_row, cols = _find_header(ws)
        if header_row is None:
            raise ValueError("could not detect a question column; pass question_col")
    else:
        cols = {"question": question_col}
        if number_col is not None:
            cols["number"] = number_col
        if section_col is not None:
            cols["section"] = section_col
        header_row = header_row or 1

    rows, section = [], ""
    for r in range(header_row + 1, ws.max_row + 1):
        row = [c.value for c in ws[r]]
        def get(role):
            i = cols.get(role)
            return row[i] if i is not None and i < len(row) else None
        if cols.get("section") is not None and get("section"):
            section = str(get("section")).strip()
        q = get("question")
        if q is None or not str(q).strip():
            continue
        num = get("number")
        rows.append({
            "number": ("" if num is None else str(num).strip()),
            "section": section,
            "text": str(q).strip(),
        })
    meta = {"sheet": ws.title, "header_row": header_row, "columns": cols,
            "row_count": len(rows)}
    return meta, rows


def build_answers_workbook(assessment: dict, rows: list[dict]) -> bytes:
    """rows = [{number, section, text, response_value, comment, final_status, evidence}]."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Responses"
    ws.append([f"Assessment: {assessment.get('title', '')}",
               f"Bank: {assessment.get('bank_name', '')}",
               f"Status: {assessment.get('status', '')}"])
    ws.append([])
    headers = ["S.No", "Section", "Control Question", "Response", "Comments",
               "Status", "Evidence"]
    ws.append(headers)
    for row in rows:
        ws.append([row.get("number", ""), row.get("section", ""), row.get("text", ""),
                   (row.get("response_value") or "").upper(), row.get("comment") or "",
                   row.get("final_status") or row.get("workflow_status") or "",
                   row.get("evidence") or ""])
    widths = [10, 22, 70, 12, 40, 16, 24]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
