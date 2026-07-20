"""Append-only activity log — every mutation should call log().

A compliance product must survive its own audit (see docs/phase1 NFRs), so this
is write-only and best-effort: a logging failure must never break the request.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy import insert

from api.database import engine, t


def log(
    *,
    tenant_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
) -> None:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with engine.begin() as conn:
            conn.execute(insert(t("activity_log")).values(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=json.dumps(detail) if detail else None,
                created_at=now,
            ))
    except Exception:  # logging must not break the caller
        pass
