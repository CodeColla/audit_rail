"""XLSX import parsing and export building (M6), via openpyxl.

Import: generalizes the Phase-0 stdlib parser — detect a header row and the
question / number / section columns (or take them as hints), then yield rows.
Export: build a clean answers workbook from an assessment's responses.
"""

from __future__ import annotations

import csv
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


_INTERROGATIVE = (
    "do ", "does ", "did ", "is ", "are ", "was ", "were ", "has ", "have ", "how ",
    "what ", "which ", "when ", "where ", "why ", "who ", "can ", "could ", "would ",
    "list ", "describe ", "provide ", "explain ", "specify ", "state ", "confirm ",
    "share ", "detail ", "mention ",
)


def _question_score(v) -> float:
    """How much a cell reads like a CHECKLIST QUESTION rather than an answer or a label.

    Length alone is not enough: a vendor's answer ("Security policies are reviewed
    annually…") is just as long as the question it answers, and picking the answer column
    imports the wrong text entirely. Interrogative shape is what separates them.
    """
    s = str(v).strip() if v is not None else ""
    if len(s) < 12 or " " not in s:
        return 0.0
    if s.endswith("?"):
        return 1.0
    if s.lower().startswith(_INTERROGATIVE):
        return 0.9
    return 0.35 if len(s) >= 25 else 0.0        # prose, but not obviously a question


def _column_quality(ws, header_row: int, col: int, sample: int = 30) -> float:
    """Fraction of the cells BELOW a header that actually read like questions.

    Header words alone are not enough to identify the question column: banks label answer
    columns things like "Control Applicability (Yes/No)", which matches the 'control' hint
    but is full of "Yes". Looking at the data underneath is what tells them apart.
    """
    seen, score = 0, 0.0
    for r in range(header_row + 1, min(header_row + 1 + sample, (ws.max_row or 0) + 1)):
        row = ws[r]
        v = row[col].value if col < len(row) else None
        if v is None or not str(v).strip():
            continue
        seen += 1
        score += _question_score(v)
    return score / seen if seen else 0.0


def _find_header(ws, scan_rows: int = 12, min_quality: float = 0.5):
    """Return (header_row_idx, {role: col_idx}) by scanning the first rows.

    Two passes: first trust the header words, but only if the column beneath them really
    holds questions; otherwise fall back to whichever column in the scan window has the
    most question-like content. Without the second pass, sheets whose real header sits
    below a title row (or which have no header at all) imported 'S.No.' / 'Yes' as the
    question text.
    """
    best_fallback = (0.0, None, None)          # (quality, row, col)

    for r in range(1, min(scan_rows, ws.max_row or 1) + 1):
        cells = [_norm(c.value) for c in ws[r]]
        # track the best-looking data column under this row, for the fallback pass
        for i in range(len(cells)):
            qual = _column_quality(ws, r, i)
            if qual > best_fallback[0]:
                best_fallback = (qual, r, i)

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
                # prefer a stronger header word, but only among columns that hold questions
                if strength > q_strength and _column_quality(ws, r, i) >= min_quality:
                    q_best, q_strength = i, strength
            elif role and role not in cols:
                cols[role] = i
        if q_best is not None:
            cols["question"] = q_best
            return r, cols

    qual, r, i = best_fallback
    if r is not None and qual >= min_quality:
        return r, {"question": i}
    return None, {}


def _is_csv(data: bytes) -> bool:
    """XLSX is a zip archive, so it always starts with 'PK'. Anything else we treat as text."""
    return not data[:2] == b"PK"


def _workbook_from_csv(data: bytes):
    """Turn CSV bytes into an in-memory workbook so ONE parsing path serves both formats.

    The UI has always advertised `accept=".xlsx,.csv"`, but the parser was openpyxl-only
    and a CSV died with an opaque BadZipFile. Sniffing the delimiter handles the
    comma/semicolon/tab variants banks actually send.
    """
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    wb = Workbook()
    ws = wb.active
    for row in csv.reader(io.StringIO(text), dialect):
        ws.append(row if row else [None])
    return wb


def parse_checklist(data: bytes, sheet: str | None = None,
                    question_col: int | None = None, number_col: int | None = None,
                    section_col: int | None = None, header_row: int | None = None):
    """Return (meta, rows). rows = [{number, section, text}] with section forward-filled."""
    if _is_csv(data):
        wb = _workbook_from_csv(data)
    else:
        # not read_only: in read_only mode ws.max_row can be None for some files
        wb = load_workbook(io.BytesIO(data), read_only=False, data_only=True)

    if sheet:
        if sheet not in wb.sheetnames:
            # Silently falling back to the active sheet imported the WRONG data on a typo.
            raise ValueError(
                f"no sheet named {sheet!r}; this file has: {', '.join(wb.sheetnames)}")
        ws = wb[sheet]
    else:
        ws = wb.active

    if question_col is None:
        header_row, cols = _find_header(ws)
        if header_row is None and not sheet:
            # The active sheet is often a leftover empty "Sheet2" — the real checklist is
            # on another tab. Try them all before giving up.
            for cand in wb.worksheets:
                if cand is ws:
                    continue
                hr, c = _find_header(cand)
                if hr is not None:
                    ws, header_row, cols = cand, hr, c
                    break
        if header_row is None:
            raise ValueError(
                "could not detect a question column — pass question_col (a column letter), "
                f"or name the sheet. Sheets in this file: {', '.join(wb.sheetnames)}")
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
