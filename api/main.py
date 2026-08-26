import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.core.config import settings
from api.core.database import engine, reflect_schema
from api.core.logging import configure_logging, logger
from api.routers import (assessments, auth, dashboard, documents, evidence, frameworks,
                         integrations, library, lookups, notifications, org,
                         people, policies, registers, roles, signing, tasks, templates)


def _preflight() -> None:
    """Fail fast with an actionable message if Postgres isn't reachable/seeded."""
    try:
        with engine.connect() as conn:
            seeded = conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='tenants')")).scalar()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Cannot reach PostgreSQL at {engine.url.render_as_string(hide_password=True)} "
            f"({e.__class__.__name__}).\n"
            "  Start it with:  docker compose up -d   (host port 5434)"
        ) from e
    if not seeded:
        raise RuntimeError(
            "PostgreSQL is up but the schema is missing.\n"
            "  Run:  .venv/bin/python scripts/init_db.py --force"
        )
    _check_schema_drift()


#: Columns this codebase reads or writes that arrived AFTER a table first existed, and the
#: hand-run SQL that adds each one. There is no Alembic here — `db/schema.sql` is the source of
#: truth and long-lived databases are migrated by hand (see docs/phase6) — so a dev or staging
#: database silently runs a schema older than the code.
#:
#: The failure mode without this check is genuinely bad: SQLAlchemy reflects the LIVE schema at
#: startup, so a missing column is not noticed at boot. It surfaces later as
#: `CompileError: Unconsumed column names: purpose` — a 500 on one route, in a stack trace that
#: names SQLAlchemy's compiler and not the actual problem. P6-S5 shipped exactly that.
#:
#: Add a line here in the same commit as any `ALTER TABLE` in the docs.
_REQUIRED_COLUMNS: list[tuple[str, str, str]] = [
    ("tenants", "logo_file_id",
     "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_file_id text;"),
    ("files", "purpose",
     "ALTER TABLE files ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'GENERIC';"),
    ("assets", "last_seen_at",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS last_seen_at iso_ts;"),
    ("assets", "expected_heartbeat_minutes",
     "ALTER TABLE assets ADD COLUMN IF NOT EXISTS expected_heartbeat_minutes integer "
     "CHECK (expected_heartbeat_minutes > 0);"),
]


def _check_schema_drift() -> None:
    """Refuse to start against a database older than the code, and say how to fix it."""
    with engine.connect() as conn:
        present = {(r[0], r[1]) for r in conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public'"))}
    missing = [(tbl, col, sql) for tbl, col, sql in _REQUIRED_COLUMNS
               if (tbl, col) not in present]
    if missing:
        lines = "\n".join(f"  {sql}" for _t, _c, sql in missing)
        cols = ", ".join(f"{tbl}.{col}" for tbl, col, _s in missing)
        raise RuntimeError(
            f"The database is missing columns this build needs: {cols}.\n"
            f"Apply them (psql, or scripts/init_db.py --force to rebuild from scratch):\n"
            f"{lines}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    _preflight()
    reflect_schema()
    if settings.jwt_secret == "dev-only-insecure-secret-change-me":
        logger.warning(
            "JWT_SECRET is the insecure dev default. "
            "Set a real secret in .env before putting any real data here."
        )
    scheduler = None
    if settings.scheduler_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler
        from api.domain.tasks_engine import run_maintenance
        run_maintenance()  # catch up on boot
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(run_maintenance, "interval",
                          minutes=settings.scheduler_interval_minutes)
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    engine.dispose()


app = FastAPI(
    title="Audit Rail API",
    description="Compliance & audit portal backend — powered by SR",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router stays open (login must be reachable). Everything else is
# default-deny: no valid token, no data. Role checks layer on inside routers.
app.include_router(auth.router, prefix="/api")

# These routers inject the principal per-endpoint (tenant scoping); templates
# is protected at the router level. All are default-deny either way.
app.include_router(library.router, prefix="/api")
app.include_router(frameworks.router, prefix="/api")
app.include_router(org.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.include_router(policies.router, prefix="/api")
app.include_router(assessments.router, prefix="/api")
app.include_router(people.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(registers.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(lookups.router, prefix="/api")

# PUBLIC, unauthenticated: the attestation magic link. Safe by construction — every
# row is derived from the token (see api/routers/signing.py). Mounted under /api so the
# SPA's Vite proxy reaches it; the user-facing link is the SPA page /sign/{token}.
app.include_router(signing.router, prefix="/api")

# Machine callers (agents/scripts), not the SPA. Token issue/list/revoke are JWT+RBAC gated;
# the exchange endpoint is unauthenticated by design, same as signing (see
# api/core/service_auth.py / api/routers/integrations.py).
app.include_router(integrations.router, prefix="/api")


# Test-only hooks for the browser suite — states a browser cannot reach (e.g. a password
# that is 30 days old). Off unless E2E_TEST_HOOKS=1, which only playwright.config.ts sets.
if os.environ.get("E2E_TEST_HOOKS") == "1":
    from api.routers import e2e_hooks
    app.include_router(e2e_hooks.router, prefix="/api")
    logger.info("E2E_TEST_HOOKS enabled — /api/e2e/* is mounted. Never enable this in production.")


@app.get("/")
def root():
    return {"status": "ok", "service": "Audit Rail API", "version": "0.1.0"}
