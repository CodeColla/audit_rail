#!/usr/bin/env python3
"""M2 — build the canonical control library and map the 470 bank questions to it.

Approach (stdlib-only, all mappings 'suggested' for human confirm):

  1. CURATED FRAMEWORK — a hand-authored standard control set (~95 controls
     across the 16 domains), written from the three checklists + common
     frameworks (ISO 27001 / RBI outsourcing). This is the vendor's own
     framework; refs are `AM 4.a` style. Lexical clustering of the raw
     questions was tried first but only merges near-duplicates (~453 controls):
     cross-bank phrasing differs too much for literal matching, and true
     consolidation needs curation (or a later semantic/LLM pass).

  2. NEAREST-CONTROL MAPPING — each bank question is classified into a domain
     (keyword scoring) and mapped to the best-matching curated control in that
     domain (token-overlap argmax). confidence = overlap score; low scores are
     the ones a human should review. This yields real many-to-one crosswalks
     (e.g. ICICI 9.3 + Kotak #85 → AM 4.a).

Idempotent: clears controls + mappings before rebuilding.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text as sql  # noqa: E402  (aliased: `text` is a local var below)

from _db import get_engine  # noqa: E402
from api.control_library import F  # noqa: E402  (single source of truth — P5-S9)
from api.mapping import classify, overlap, toks  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# KIAM-specific: an on-prem vendor with no cloud or AI footprint. Deliberately NOT
# carried into api/control_library.seed() — see the note there.
DORMANT = {"CS", "AI"}

def uid() -> str:
    return str(uuid.uuid4())


def main() -> None:
    import sys as _sys
    force = "--force" in _sys.argv[1:]

    engine = get_engine()
    with engine.begin() as con:
        tenant_id = con.execute(sql("SELECT id FROM tenants LIMIT 1")).scalar()
        dom_id = {code: id_ for id_, code in
                  con.execute(sql("SELECT id, code FROM domains WHERE tenant_id = :t"),
                             {"t": tenant_id})}

        # P4-S5: this script was written for a one-time bootstrap and unconditionally
        # DELETEd controls/question_control_map/evidence_controls with NO tenant_id
        # filter — wiping every organisation's data, not just the one it picked with
        # `LIMIT 1`. Controls are now real editable data (stock_response set by hand,
        # evidence and policies linked to them), so a careless re-run would also
        # silently destroy work no seed script created. Both problems get one fix: scope
        # every statement to the chosen tenant, and refuse to touch it if anything
        # already depends on its controls, unless the caller passes --force.
        if not force:
            in_use = con.execute(sql("""
                SELECT
                    (SELECT count(*) FROM controls
                      WHERE tenant_id = :t AND stock_response IS NOT NULL) AS answered,
                    (SELECT count(*) FROM tasks
                      WHERE tenant_id = :t AND control_id IS NOT NULL) AS tasks,
                    (SELECT count(*) FROM responses
                      WHERE tenant_id = :t AND prefilled_from_control_id IS NOT NULL) AS responses,
                    (SELECT count(*) FROM evidence_controls WHERE tenant_id = :t) AS evidence,
                    (SELECT count(*) FROM control_documents WHERE tenant_id = :t) AS documents
            """), {"t": tenant_id}).mappings().first()
            blockers = {k: v for k, v in in_use.items() if v}
            if blockers:
                print("Refusing to rebuild — this tenant's controls are already in use:")
                for k, v in blockers.items():
                    print(f"  {k}: {v}")
                print("Re-run with --force to rebuild anyway (this WILL delete the above).")
                return

        # idempotent rebuild — children first (Postgres enforces FKs natively).
        # Every DELETE is scoped to `tenant_id`: this script picks ONE org with
        # `LIMIT 1`, and an unscoped DELETE would wipe every other org's data too.
        for tbl in ("question_control_map", "control_evidence_requirements",
                    "evidence_controls", "control_documents", "controls"):
            con.execute(sql(f"DELETE FROM {tbl} WHERE tenant_id = :t"), {"t": tenant_id})

        # insert curated controls; keep ref -> (control_id, token-set) for matching
        control_ix: dict[str, list[tuple[str, str, set]]] = {}
        for code, defs in F.items():
            for ref, stmt, life, months, kw in defs:
                cid = uid()
                dormant = code in DORMANT
                con.execute(sql(
                    "INSERT INTO controls (id,tenant_id,domain_id,code,statement,"
                    "lifecycle,recurrence_months,applicability,na_justification,"
                    "reactivation_trigger,stock_response,status,created_at,updated_at) "
                    "VALUES (:i,:t,:d,:c,:s,:l,:m,:a,:nj,:rt,:sr,:st,:ca,:ua)"),
                    {"i": cid, "t": tenant_id, "d": dom_id[code], "c": ref, "s": stmt,
                     "l": life, "m": months,
                     "a": "not_applicable" if dormant else "applicable",
                     "nj": "No cloud/AI services in scope; on-prem delivery." if dormant else None,
                     "rt": ("Cloud adoption" if code == "CS" else "AI tool in scope") if dormant else None,
                     "sr": "na" if dormant else None,
                     "st": "active", "ca": NOW, "ua": NOW})
                control_ix.setdefault(code, []).append((cid, ref, toks(stmt) | toks(kw)))

        # map every question to the best curated control in its domain
        rows = con.execute(sql("""
            SELECT q.id, q.text, COALESCE(s.title,''), t.bank_name
            FROM questions q JOIN templates t ON t.id = q.template_id
            LEFT JOIN template_sections s ON s.id = q.section_id""")).all()

        n_maps = low_conf = 0
        for qid, qtext, section, bank in rows:
            code = classify(qtext, section)
            qtok = toks(qtext)
            cands = control_ix.get(code) or control_ix["GRP"]
            best_cid, best_score = None, -1.0
            for cid, ref, ctok in cands:
                score = overlap(qtok, ctok)
                if score > best_score:
                    best_cid, best_score = cid, score
            con.execute(sql(
                "INSERT INTO question_control_map (id,question_id,control_id,confidence,"
                "status,created_at) VALUES (:i,:q,:c,:cf,:s,:ca)"),
                {"i": uid(), "q": qid, "c": best_cid, "cf": round(best_score, 3),
                 "s": "suggested", "ca": NOW})
            n_maps += 1
            if best_score < 0.20:
                low_conf += 1

    total_controls = sum(len(v) for v in F.values())
    print(f"curated controls : {total_controls}")
    print(f"questions mapped : {n_maps}/{len(rows)}  "
          f"(low-confidence <0.20 needing review: {low_conf})")

    with engine.connect() as con:
        print("\nby domain (controls · mapped questions):")
        for code, name, cc, mc in con.execute(sql("""
            SELECT d.code, d.name, COUNT(DISTINCT c.id), COUNT(m.id)
            FROM domains d
            LEFT JOIN controls c ON c.domain_id = d.id
            LEFT JOIN question_control_map m ON m.control_id = c.id
            GROUP BY d.id, d.code, d.name, d.sort_order ORDER BY d.sort_order""")):
            print(f"  {code:4} {name:32} {cc:3} · {mc:3}")
        print("\ntop crosswalks (one control ← many bank questions):")
        for ref, n in con.execute(sql("""
            SELECT c.code, COUNT(m.id) n FROM controls c
            JOIN question_control_map m ON m.control_id = c.id
            GROUP BY c.id, c.code ORDER BY n DESC LIMIT 6""")):
            print(f"  {ref}: {n} questions")


if __name__ == "__main__":
    main()
