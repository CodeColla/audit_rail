#!/usr/bin/env python3
"""Extract bank audit checklist controls from the .xlsx files in data/ into
structured CSV + JSON under data/extracted/.

Stdlib-only on purpose (zipfile + ElementTree xlsx parsing) so it runs on a
bare python3 with no pip installs.

Sources (see docs/phase0/01-checklist-anatomy.md for full anatomy):
  1. (1)_VRA_Assessment Checklist v1.2            -> flat domain/question/response
  2. (2)_Updated Annexure C ... v2.7              -> 2-LOD due-diligence questionnaire
  3. (2)_Updated_KSL_IS_Vendor_Risk_Assessment... -> sectioned multi-reviewer questionnaire
"""

import csv
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
OUT = DATA / "extracted"

VRA_FILE = "(1)_VRA_Assessment Checklist v1.2 (1) (1) (2) (4).xlsx"
ANNEX_FILE = "(2)_Updated Annexure C Pre-Onboarding Assessment Questionnaire - v2.7.xlsx"
KSL_FILE = "(2)_Updated_KSL_IS_Vendor_Risk_Assessment_Checklist_V3.0 - Vendor response (1).xlsx"


# ---------------------------------------------------------------- xlsx reader

def load_workbook(path):
    """Return (sheet_name -> {row_number -> {col_letter: value}}) for a .xlsx."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si"):
            shared.append("".join(t.text or "" for t in si.iter(M + "t")))
    rels = {
        rel.get("Id"): rel.get("Target")
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    }
    sheets = {}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    for sh in wb.find(M + "sheets"):
        target = rels[sh.get(R + "id")]
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        sheets[sh.get("name")] = target

    book = {}
    for name, target in sheets.items():
        rows = {}
        root = ET.fromstring(z.read(target))
        sheet_data = root.find(M + "sheetData")
        if sheet_data is None:
            book[name] = rows
            continue
        for row in sheet_data:
            cells = {}
            for c in row.findall(M + "c"):
                col = re.match(r"([A-Z]+)", c.get("r", "")).group(1)
                v = c.find(M + "v")
                if v is None:  # inlineStr / formula-only cells
                    val = "".join(t.text or "" for t in c.iter(M + "t"))
                else:
                    val = v.text or ""
                    if c.get("t") == "s":
                        val = shared[int(val)]
                val = val.strip()
                if val:
                    cells[col] = val
            if cells:
                rows[int(row.get("r"))] = cells
        book[name] = rows
    return book


# ------------------------------------------------------------- source parsers

def parse_vra(book):
    """(1) VRA v1.2 — Sheet1: A domain | B control no | C question | D response | E comments."""
    rows = book["Sheet1"]
    controls, domain = [], ""
    for r in sorted(rows):
        if r == 1:
            continue  # header
        d = rows[r]
        domain = d.get("A", domain)
        if not d.get("C"):
            continue
        controls.append({
            "source": "VRA_v1.2",
            "sheet": "Sheet1",
            "row": r,
            "domain": domain,
            "sub_domain": "",
            "control_no": d.get("B", ""),
            "question": d["C"],
            "vendor_response": d.get("D", ""),
            "vendor_comments": d.get("E", ""),
        })
    return controls


ANNEX_LOD1 = {  # columns N..V, group header "1st LOD Assessor Evaluation (From Bank)"
    "N": "lod1_response", "O": "lod1_comments", "P": "lod1_likelihood_rating",
    "Q": "lod1_likelihood_score", "R": "lod1_impact_rating", "S": "lod1_impact_score",
    "T": "lod1_risk_rating", "U": "lod1_risk_statement", "V": "lod1_recommendation",
}
ANNEX_LOD2 = {  # columns W..AF, group header "2nd LOD Assessor Evaluation (From Bank)"
    "W": "lod2_assigned_ownership", "X": "lod2_response", "Y": "lod2_comments",
    "Z": "lod2_likelihood_rating", "AA": "lod2_likelihood_score",
    "AB": "lod2_impact_rating", "AC": "lod2_impact_score", "AD": "lod2_risk_rating",
    "AE": "lod2_issue_description", "AF": "lod2_recommendation",
}


def parse_annexure(book):
    """(2) Annexure C v2.7 — 'Due Dilligence Questionnaire', headers r4/r5, data r6+.

    Domain (D), sub-domain (F) and rationale (C) live in merged cells: they are
    only present on the first row of their block, so forward-fill them.
    """
    rows = book["Due Dilligence Questionnaire"]
    controls = []
    rationale = domain = sub_domain = applicable = ""
    for r in sorted(rows):
        d = rows[r]
        if not d.get("B", "").isdigit():
            continue
        if d.get("D"):  # new domain block: don't leak the previous block's
            domain = d["D"]  # rationale/sub-domain into it
            rationale = d.get("C", "")
            sub_domain, applicable = "", ""
        if d.get("F"):
            sub_domain = d["F"]
            applicable = d.get("E", "")
        review = {v: d.get(k, "") for k, v in {**ANNEX_LOD1, **ANNEX_LOD2}.items()}
        controls.append({
            "source": "AnnexureC_v2.7",
            "sheet": "Due Dilligence Questionnaire",
            "row": r,
            "control_no": d["B"],
            "rationale": rationale,
            "domain": domain,
            "sub_domain": sub_domain,
            "sub_domain_applicable": applicable,
            "question": d.get("G", ""),
            "classification": d.get("H", ""),
            "testing_procedure": d.get("I", ""),
            "mandatory_evidence": d.get("J", ""),
            "vendor_response": d.get("K", ""),
            "vendor_comments": d.get("L", ""),
            "evidence": d.get("M", ""),
            **review,
        })
    return controls


def parse_ksl(book):
    """(3) KSL V3.0 — 'VRA_Questionaire'.

    Layout quirks handled here:
      - r1 doubles as the column header row AND major-section 1 title (in B).
      - Major sections: A = section number, B = short title, and none of the
        reviewer columns (F/G/H) are filled. Controls always have F, G or H.
      - Sub-sections: A = text, B empty.
      - Column L is a data-validation legend, ignored.
      - One question (r140) has no number in the source; kept with control_no "".
    """
    rows = book["VRA_Questionaire"]
    controls = []
    section = sub_section = ""
    for r in sorted(rows):
        d = rows[r]
        a, b = d.get("A", ""), d.get("B", "")
        if r == 1:  # header row; B carries the first major-section title
            section, sub_section = b, ""
            continue
        if a and not a.isdigit():
            sub_section = a
            continue
        is_section = (
            a.isdigit() and b and len(b) < 60 and "?" not in b
            and not any(k in d for k in ("F", "G", "H"))
        )
        if is_section:
            section, sub_section = b, ""
            continue
        if not b and not d.get("C"):
            continue
        # unnumbered question rows keep their text in B (or C when B is empty)
        question = b or d.get("C", "")
        controls.append({
            "source": "KSL_v3.0",
            "sheet": "VRA_Questionaire",
            "row": r,
            "domain": section,
            "sub_domain": sub_section,
            "control_no": a,
            "question": question,
            "vendor_response": d.get("C", "") if b else "",
            "vendor_comments": d.get("D", ""),
            "status": d.get("E", ""),
            "infosec_comments": d.get("F", ""),
            "soc_remarks": d.get("G", ""),
            "spoc_comments": d.get("H", ""),
        })
    return controls


# -------------------------------------------------------------------- outputs

def write_csv(path, records):
    fields = []
    for rec in records:  # union of keys, first-seen order
        for k in rec:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sources = [
        (VRA_FILE, parse_vra, "vra_checklist_v1_2.csv"),
        (ANNEX_FILE, parse_annexure, "annexure_c_v2_7.csv"),
        (KSL_FILE, parse_ksl, "ksl_vra_v3_0.csv"),
    ]
    everything = []
    for fname, parser, out_csv in sources:
        book = load_workbook(DATA / fname)
        records = parser(book)
        for rec in records:
            rec["source_file"] = fname
        write_csv(OUT / out_csv, records)
        everything.extend(records)
        domains = sorted({rec["domain"] for rec in records})
        print(f"{out_csv}: {len(records)} controls, {len(domains)} domains")
        for dom in domains:
            n = sum(1 for rec in records if rec["domain"] == dom)
            print(f"    {n:3d}  {dom}")
    with open(OUT / "all_controls.json", "w", encoding="utf-8") as f:
        json.dump(everything, f, indent=2, ensure_ascii=False)
    print(f"\nall_controls.json: {len(everything)} controls total")


if __name__ == "__main__":
    main()
