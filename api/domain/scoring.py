"""Scoring (M6) — weighted response score → verdict, per-template configurable.

Config shape (stored as JSON in scoring_configs.config, see
docs/phase2/02-design-handoff-notes.md):

    {
      "response_weights": {"yes":1.0,"partial":0.5,"no":0.0,"na":null},
      "na_handling": "excluded",
      "verdict_thresholds": [
        {"verdict":"Satisfactory","min_pct":85},
        {"verdict":"Conditional","min_pct":70},
        {"verdict":"Pending","min_pct":0}
      ]
    }
"""

from __future__ import annotations

import json

from sqlalchemy import select

from api.core.database import t

DEFAULT_CONFIG = {
    "response_weights": {"yes": 1.0, "partial": 0.5, "no": 0.0, "na": None},
    "na_handling": "excluded",
    "verdict_thresholds": [
        {"verdict": "Satisfactory", "min_pct": 85},
        {"verdict": "Conditional", "min_pct": 70},
        {"verdict": "Pending", "min_pct": 0},
    ],
}


def config_for_template(conn, template_id: str) -> dict:
    row = conn.execute(
        select(t("scoring_configs").c.config)
        .where(t("scoring_configs").c.template_id == template_id)
    ).scalar()
    if not row:
        return DEFAULT_CONFIG
    try:
        return json.loads(row)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG


def evaluate(values: list[str | None], config: dict | None = None) -> dict:
    """values = response_value per answered question. Returns score% + verdict."""
    config = config or DEFAULT_CONFIG
    weights = config.get("response_weights", DEFAULT_CONFIG["response_weights"])
    num = den = 0.0
    for v in values:
        if v is None:
            continue
        w = weights.get(v)
        if w is None:  # excluded from the denominator (e.g. N/A)
            continue
        num += w
        den += 1
    pct = round(100 * num / den) if den else 0
    verdict = None
    for band in sorted(config.get("verdict_thresholds", []),
                       key=lambda b: b["min_pct"], reverse=True):
        if pct >= band["min_pct"]:
            verdict = band["verdict"]
            break
    return {"score_pct": pct, "scored": int(den), "verdict": verdict or "Pending"}
