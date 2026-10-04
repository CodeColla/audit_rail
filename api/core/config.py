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
    # PostgreSQL (docker-compose.yml). Host port is 5434: Probo holds 5432 and
    # emp_erp_mcp holds 5433 on this machine. db/schema.sql stays the canonical
    # schema (no ORM models).
    database_url: str = (
        "postgresql+psycopg://audit:audit@localhost:5434/audit_rail"
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

    # ── Deployment mode (Phase 10 — Enterprise licensing) ───────────────────
    # "public": the shared, multi-tenant SaaS — Free/Pro/Enterprise plans via tenant_plans,
    # Pro sold through Razorpay (api/domain/plans.py, entitlements.py).
    # "enterprise": a standalone, single-tenant deployment (the customer's own infra, or a
    # dedicated instance on ours) with NO payment gateway — entitlement comes from a signed
    # RS256 license token instead (api/domain/licensing.py). Same Docker image either way;
    # only this flag (and, for enterprise, applying a license) differs.
    deployment_mode: str = "public"
    # The PUBLIC half of an offline-generated RSA keypair — safe to ship in every image,
    # a Public deployment just never reads it. The PRIVATE half never leaves the vendor's
    # own machine; see scripts/generate_license_keypair.py and scripts/issue_license.py.
    license_public_key_path: str = os.path.join(BASE_DIR, "license_public_key.pem")
    # How long a license keeps its last-known numbers after it expires (or how long a
    # brand-new enterprise deployment runs unlimited before one is ever applied) before
    # usage is frozen at its current level. See api/domain/entitlements.py.
    license_grace_days: int = 30

    # ── Razorpay (Phase 10, S3a) — Public mode only; never read in enterprise mode ────
    # Empty by default: every billing route 503s "not configured" rather than crashing,
    # so pytest and e2e stay fully offline (api/routers/billing.py). Test-mode values go
    # in .env (gitignored); live values go in _docker/env/api.env, never committed.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    # Set in the Razorpay dashboard when the webhook is created — a value YOU choose,
    # never derived from razorpay_key_secret. Verifies api/domain/billing.verify_webhook.
    razorpay_webhook_secret: str = ""
    # Razorpay `plan_…` ids — created once per environment (scripts/create_razorpay_plans.py),
    # and DIFFERENT between test and live, so these are never hard-coded in api/domain/plans.py
    # the way prices and limits are.
    razorpay_plan_pro_monthly: str = ""
    razorpay_plan_pro_yearly: str = ""
    razorpay_plan_addon_audits_monthly: str = ""
    razorpay_plan_addon_audits_yearly: str = ""
    razorpay_plan_addon_storage_monthly: str = ""
    razorpay_plan_addon_storage_yearly: str = ""
    # How long a Pro subscription keeps its limits after Razorpay reports a failed
    # charge (status pending/halted) before api/domain/billing.sync_tenant_plan drops it
    # to Free. See api/domain/entitlements.py's existing past_due/grace_until handling —
    # this is the number that FILLS that column; entitlements.py itself just reads it.
    billing_grace_days: int = 7
    # 0 until SR Associates registers for GST (req/phase10/00-issue-19.md §1) — kept as a
    # setting, not a hard-coded 0, so switching it on is a config change, not a redeploy.
    gst_rate: float = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
