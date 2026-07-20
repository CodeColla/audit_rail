"""
Central runtime configuration — every tunable lives here, loaded from .env.

Import `settings` (a cached singleton). Nothing else in api/ should read
os.environ directly.
"""

import os
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ────────────────────────────────────────────────────────────
    # PostgreSQL (docker-compose.yml). Host port is 5433, not 5432 — Probo's
    # stack already holds 5432 on this machine. db/schema.sql stays the
    # canonical schema (no ORM models).
    database_url: str = (
        "postgresql+psycopg://audit:audit@localhost:5433/audit_rail"
    )

    # ── API server ──────────────────────────────────────────────────────────
    # 5007/3002 so emp_erp_mcp (5005/3001) can run alongside.
    api_host: str = "127.0.0.1"
    api_port: int = 5007
    cors_origins: List[str] = ["http://localhost:3002"]

    # ── Auth (M1) ───────────────────────────────────────────────────────────
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 12

    # ── Evidence vault (local disk now, S3-compatible later — decision D4) ──
    vault_dir: str = os.path.join(BASE_DIR, "data", "vault")
    max_upload_mb: int = 25

    # ── Recurring-task scheduler (M5) ───────────────────────────────────────
    # Disabled in tests (deterministic); the engine functions are called
    # directly there. In dev it flips overdue runs on an interval.
    scheduler_enabled: bool = True
    scheduler_interval_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
