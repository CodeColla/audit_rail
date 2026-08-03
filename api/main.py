import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.config import settings
from api.database import engine, reflect_schema
from api.routers import (assessments, auth, dashboard, documents, evidence, frameworks,
                         library, lookups, notifications, people, policies, registers, roles,
                         signing, tasks, templates)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _preflight()
    reflect_schema()
    if settings.jwt_secret == "dev-only-insecure-secret-change-me":
        print(
            "\n  WARNING: JWT_SECRET is the insecure dev default. "
            "Set a real secret in .env before putting any real data here.\n"
        )
    scheduler = None
    if settings.scheduler_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler
        from api.tasks_engine import run_maintenance
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
    title="audit_rail API",
    description="Compliance/audit portal backend — SR",
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


# Test-only hooks for the browser suite — states a browser cannot reach (e.g. a password
# that is 30 days old). Off unless E2E_TEST_HOOKS=1, which only playwright.config.ts sets.
if os.environ.get("E2E_TEST_HOOKS") == "1":
    from api.routers import e2e_hooks
    app.include_router(e2e_hooks.router, prefix="/api")
    print("  E2E_TEST_HOOKS enabled — /api/e2e/* is mounted. Never enable this in production.")


@app.get("/")
def root():
    return {"status": "ok", "service": "audit_rail API", "version": "0.1.0"}
