"""Billing plans (Phase 10, S2a) — req/phase10/00-issue-19.md §1.

The vocabulary lives here rather than in a table, same posture as
`api/core/permissions.py`'s `MODULES`: a price or limit change is a deploy, not a
migration. `GET /api/billing/plans` serves these constants to the frontend so the UI
never hard-codes a number either — and `landingpage/src/lib/pricing.ts` mirrors them for
the marketing page, which has no backend to call.

`tenant_plans` (db/schema.sql) holds the EFFECTIVE plan per tenant; `api/domain/
entitlements.py` combines a row from this module with that table's add-ons and overrides.
"""

from __future__ import annotations

from typing import Literal, TypedDict

PlanCode = Literal["free", "pro", "enterprise"]
BillingCycle = Literal["monthly", "yearly"]

MB = 1024 * 1024
GB = 1024 * MB


class Plan(TypedDict):
    name: str
    #: None = unlimited (Pro's `max_members`) or "priced per deal" (Enterprise's price).
    max_active_audits: int | None
    storage_bytes: int | None
    max_members: int | None
    price_paise: dict[BillingCycle, int] | None


PLANS: dict[PlanCode, Plan] = {
    "free": {
        "name": "Free",
        "max_active_audits": 1,
        "storage_bytes": 500 * MB,
        "max_members": 3,
        "price_paise": {"monthly": 0, "yearly": 0},
    },
    "pro": {
        "name": "Pro",
        "max_active_audits": 10,
        "storage_bytes": 25 * GB,
        "max_members": None,
        "price_paise": {"monthly": 249_900, "yearly": 2_499_000},
    },
    "enterprise": {
        "name": "Enterprise",
        "max_active_audits": None,
        "storage_bytes": None,
        "max_members": None,
        "price_paise": None,  # priced per deal — never sold through Razorpay checkout
    },
}


class Addon(TypedDict):
    name: str
    #: What one pack of this add-on adds to the Pro plan's own limit.
    audits: int
    storage_bytes: int
    price_paise: dict[BillingCycle, int]


ADDONS: dict[str, Addon] = {
    "audits": {
        "name": "+5 active audits",
        "audits": 5,
        "storage_bytes": 0,
        "price_paise": {"monthly": 99_900, "yearly": 999_000},
    },
    "storage": {
        "name": "+25 GB storage",
        "audits": 0,
        "storage_bytes": 25 * GB,
        "price_paise": {"monthly": 49_900, "yearly": 499_000},
    },
}

#: RBI e-mandate rule (req/phase10/00-issue-19.md §2): a recurring debit above this needs the
#: customer to approve each renewal, not just the first one. Informational only here — S3a's
#: checkout flow surfaces it; nothing in S2a enforces it.
RBI_APPROVAL_THRESHOLD_PAISE = 1_500_000
