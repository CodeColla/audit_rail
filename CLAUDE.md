# Audit Rail

Compliance & audit workspace for teams who get audited a lot: answer a bank's questionnaire
once, reuse the answer across every audit that asks the same thing. FastAPI + SQLAlchemy Core
+ PostgreSQL backend, React + TypeScript + Vite frontend. Multi-tenant throughout.

This file stays short on purpose — it's loaded into every session automatically, so only the
rules that are genuinely load-bearing everywhere live here. Deeper, subsystem-specific detail
lives in `api/CLAUDE.md` and `webui/CLAUDE.md`, loaded only when working in those trees.

## Repo map

```
api/          FastAPI backend — routers/, core/ (config, database, auth, permissions,
              logging), domain/ (business logic), rendering/ (PDF/DOCX/XLSX)
webui/        React SPA — src/pages/ (feature-grouped), src/components/, src/lib/, e2e/
db/schema.sql The single source of truth for the whole schema
tests/        pytest, rebuilt fresh from db/schema.sql on every run
req/          Raw, tracked notes from the project owner — read before planning a sprint
docs/         Sprint plans and phase write-ups — gitignored, LOCAL ONLY, never assume it
              exists on a fresh clone
_docker/      Deployment: dockerfile/, compose/, scripts/service_ctl.sh
```

## Rules that apply everywhere

- **No Alembic.** `db/schema.sql` is the only source of truth. A schema change means editing
  it AND hand-running the equivalent SQL against every live database that needs it (dev,
  `audit_rail_e2e`, and — separately, deliberately, never automatically — prod). pytest
  rebuilds its database from `db/schema.sql` fresh every run, so it can't catch a hand-run
  migration you forgot; nothing else will notice for you.
- **SQLAlchemy Core, not the ORM.** `api/core/database.py`'s `t("table_name")` reflects
  `db/schema.sql` at startup — there are no model classes to keep in sync.
- **Plan before you build.** Raw ask → `req/phaseN/`. Decided plan → `docs/phaseN/`. Once a
  sprint starts, it's execution only — new decisions go back into a plan doc, not into
  scattered inline judgment calls.
- **Comments explain WHY, never WHAT.** A well-named function already says what it does; a
  comment earns its place only for a hidden constraint, a workaround, or something that would
  otherwise surprise the next reader.
- **Logging: `loguru`, not `print`, not stdlib `logging`.** `from loguru import logger`,
  configured once in `api/main.py`. New as of issue #5 — see `api/CLAUDE.md` for the full
  pattern (including how routes should use it) with examples.
- **Nothing here blocks a commit or a merge.** These are conventions for how to write NEW
  code and code you're already touching — not a retrofit mandate and not CI enforcement
  (there is no CI in this repo). Don't mass-rewrite files just to match a rule below.

## Tests

```bash
.venv/bin/python -m pytest -q     # API tests — own throwaway DB, rebuilt from schema.sql
bash e2e.sh                       # Playwright — its OWN stack (UI 3099 / API 5099 /
                                   # audit_rail_e2e), re-seeds by default
```
