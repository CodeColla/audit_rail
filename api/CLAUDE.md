# `api/` conventions

Loaded automatically when working under `api/`. Assumes you've already read the root
`CLAUDE.md`.

## Error handling: try/except in routes, but never leak the exception

New as of issue #5, inspired by the reference repo (GINTI) — but NOT a literal copy of it.
GINTI's own pattern is `except Exception as e: raise HTTPException(500, detail=str(e))`,
which hands the raw Python exception text back to the API client. That's an
information-disclosure smell, not something worth importing.

The pattern here: **log the full exception server-side, return a generic message to the
client.**

```python
from api.core.logging import logger

@router.post("/{thing_id}/frobnicate")
def frobnicate(thing_id: str, user: Principal = Depends(require("things", "edit"))):
    try:
        with engine.begin() as conn:
            ...
        return {"ok": True}
    except HTTPException:
        raise  # an intentional 400/403/404/409 — let it through unchanged
    except Exception as e:
        logger.error(f"frobnicate failed for {thing_id}: {e}")
        raise HTTPException(500, "something went wrong — try again")
```

This is going forward, not a retrofit — most existing routers today have **no** blanket
try/except (see "What's already here" below), and that's fine; nothing needs to change just
to add one. Apply this to new routes, and to existing ones when you're touching them for
another reason anyway.

Expected business-rule failures (bad input, not found, conflict) still go straight to
`raise HTTPException(400/404/409, "a specific, actionable message")` — that message is safe
to show because YOU chose its wording; it's never `str(e)`.

## What's already here (read before assuming it's missing)

- **No ORM.** `t("table_name")` (from `api.core.database`) returns a reflected
  `sqlalchemy.Table`. Build queries with Core's `select()`/`insert()`/`update()`/`delete()`.
- **Two ways to get a connection**: `conn=Depends(get_conn)` for a read-only handler, or
  `with engine.begin() as conn:` inside the handler body for anything that writes — this
  repo's routers use `engine.begin()` almost everywhere a write happens, wrapping the
  ownership check and the write in the same transaction.
- **Tenant scoping is app-level, not RLS.** RLS policies exist in `db/schema.sql` but are
  inert (the app connects as the table owner) — every query needs its own
  `.where(x.c.tenant_id == user.tenant_id)`, or a composite FK that pins it structurally.
  Several past bugs were exactly this check missing on one route.
- **`require("module", "action")`** (from `api.core.permissions`) is the permission
  dependency — `Depends(require("audits", "edit"))` etc. Modules and actions are catalogued
  in `api/core/permissions.py`.
- **`StrictModel`** (from `api.core.util`) is the request-body base class that rejects
  unknown fields with a 422 instead of silently dropping them. Use it for new request models.
- **`activity.log(...)`** (from `api.core.activity`) records an audit-trail entry for
  anything a user does that matters later — most mutating routes call it after the write
  commits.
- **A "does this belong to the caller's tenant" helper is a per-router local, not shared** —
  e.g. `_own_template()` in `templates.py`, `_access()` in `assessments.py`. Follow that
  pattern (a small private function at the top of the router) rather than inlining the check
  at every call site.
- **`main.py`'s router registration order is deliberate**: `auth` first (must stay reachable
  pre-login), then tenant-scoped routers, then `signing` (public/unauthenticated) near the
  end, then `e2e_hooks` gated behind `E2E_TEST_HOOKS`. Keep new routers out of that ordering
  unless there's a reason to change it.
