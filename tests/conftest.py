"""Test fixtures — an isolated PostgreSQL database seeded with one tenant + users.

Requires Postgres to be up:  docker compose up -d   (host port 5434)

We use a dedicated `audit_rail_test` database (created on demand) and reset its
`public` schema at the start of the session, so tests never touch dev data.
DATABASE_URL is set before any `api.*` import so the engine singleton binds to it.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

NOW = "2026-07-16T00:00:00Z"
PG_USER, PG_PASS, PG_HOST, PG_PORT = "audit", "audit", "localhost", "5434"
ADMIN_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/audit_rail"
TEST_DB = "audit_rail_test"
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql+psycopg://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{TEST_DB}",
)

# ── point the app at the TEST database, at conftest IMPORT time ────────────────────────
# This must happen here, not inside the fixture. pytest imports conftest before any test
# module, but test modules are imported at COLLECTION time — before fixtures run. So a test
# file with a module-level `from api.x import ...` would construct api.config.settings (and
# therefore api.database.engine) while DATABASE_URL still pointed at the DEV database, and
# the whole session would then run against dev data: seeded logins would 401 and seeds would
# collide. Setting it here makes module-level api imports safe.
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.setdefault("VAULT_DIR", tempfile.mkdtemp(prefix="ar-test-vault-"))


def _ensure_test_database() -> None:
    """CREATE DATABASE audit_rail_test if it doesn't exist (needs AUTOCOMMIT)."""
    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
        with admin.connect() as c:
            exists = c.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": TEST_DB}
            ).scalar()
            if not exists:
                c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
        admin.dispose()
    except Exception as e:  # noqa: BLE001 — surface a helpful message, not a stack trace
        pytest.exit(
            f"\nCannot reach PostgreSQL at {PG_HOST}:{PG_PORT} ({e.__class__.__name__}).\n"
            f"Start it first:  docker compose up -d\n",
            returncode=1,
        )


@pytest.fixture(scope="session")
def app_client():
    _ensure_test_database()   # env already points at it — see the note above

    from _db import apply_schema, reset_schema  # noqa: E402  (after env is set)

    engine = create_engine(TEST_URL, future=True)
    with engine.begin() as conn:
        reset_schema(conn)
        apply_schema(conn)

        from api.auth import hash_password  # noqa: E402

        tid, admin_id, member_id = (str(uuid.uuid4()) for _ in range(3))
        conn.execute(text("INSERT INTO tenants (id,name,slug,status,created_at) "
                          "VALUES (:i,:n,:s,:st,:c)"),
                     {"i": tid, "n": "KIAM", "s": "kiam", "st": "active", "c": NOW})
        for uid_, email, name, pw, admin_flag in [
            (admin_id, "admin@kiam.example", "Admin", "secret1", 1),
            (member_id, "member@kiam.example", "Member", "secret2", 0),
        ]:
            conn.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,auth_provider,"
                "is_platform_admin,status,created_at) "
                "VALUES (:i,:e,:f,:p,:a,:ia,:s,:c)"),
                {"i": uid_, "e": email, "f": name, "p": hash_password(pw),
                 "a": "local", "ia": admin_flag, "s": "active", "c": NOW})
        for uid_, role in [(admin_id, "admin"), (member_id, "member")]:
            conn.execute(text("INSERT INTO tenant_members (id,tenant_id,user_id,role,"
                              "created_at) VALUES (:i,:t,:u,:r,:c)"),
                         {"i": str(uuid.uuid4()), "t": tid, "u": uid_,
                          "r": role, "c": NOW})
        conn.execute(text(
            "INSERT INTO domains (id,tenant_id,code,name,sort_order) "
            "VALUES (:i,:t,'AM','Access Management',0)"),
            {"i": str(uuid.uuid4()), "t": tid})
    engine.dispose()

    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as client:
        yield client


def token(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]
