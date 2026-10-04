"""Razorpay REST client (Phase 10, S3a) — req/phase10/00-issue-19.md,
docs/phase10/01-sprint-plan.md.

A thin `httpx` wrapper, not the official `razorpay` SDK — the surface this product needs
is about six plain REST calls with basic auth, and a thin client is trivial to fake in
tests (`tests/test_billing_razorpay.py` monkeypatches these functions directly; no
network is ever touched there). Every call logs the endpoint and the outcome — NEVER the
auth header or a request/response body, since either could carry something sensitive.

Only ever called when `settings.deployment_mode == "public"` — a standalone Enterprise
deployment has no gateway at all; `api/domain/licensing.py` is its sibling there.
"""

from __future__ import annotations

import httpx

from api.core.config import settings
from api.core.logging import logger

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT = 10.0


class RazorpayError(RuntimeError):
    """A non-2xx response, wrapped so callers get a clean message instead of an httpx
    exception whose str() might include the request."""


def _call(method: str, path: str, **kw) -> dict:
    with httpx.Client(base_url=BASE_URL, auth=(settings.razorpay_key_id,
                                               settings.razorpay_key_secret),
                      timeout=TIMEOUT) as client:
        r = client.request(method, path, **kw)
    if r.status_code >= 400:
        code = None
        try:
            code = r.json().get("error", {}).get("code")
        except Exception:  # noqa: BLE001 — a non-JSON error body is still just "unknown"
            pass
        logger.warning(f"razorpay {method} {path} -> {r.status_code} ({code})")
        raise RazorpayError(f"Razorpay {method} {path} failed: {r.status_code} {code or ''}".strip())
    logger.info(f"razorpay {method} {path} -> {r.status_code}")
    return r.json()


def create_subscription(*, plan_id: str, quantity: int, total_count: int, notes: dict) -> dict:
    return _call("POST", "/subscriptions", json={
        "plan_id": plan_id, "quantity": quantity, "total_count": total_count,
        "customer_notify": 1, "notes": notes,
    })


def get_subscription(subscription_id: str) -> dict:
    return _call("GET", f"/subscriptions/{subscription_id}")


def update_subscription_quantity(subscription_id: str, quantity: int,
                                 schedule_change_at: str = "now") -> dict:
    """Confirmed against the real test API (tests/fixtures/razorpay/
    subscription_quantity_update_rejected.json): this ONLY works once the subscription is
    `authenticated`/`active`. A subscription still in `created` (never checked out)
    rejects this with 400 — callers must cancel + recreate it instead; see
    api/routers/billing.py's add-on logic, which branches on exactly that status."""
    return _call("PATCH", f"/subscriptions/{subscription_id}", json={
        "quantity": quantity, "schedule_change_at": schedule_change_at,
    })


def cancel_subscription(subscription_id: str, *, at_cycle_end: bool = False) -> dict:
    return _call("POST", f"/subscriptions/{subscription_id}/cancel",
                json={"cancel_at_cycle_end": 1 if at_cycle_end else 0})


def create_plan(*, name: str, amount_paise: int, period: str, interval: int = 1,
                description: str = "") -> dict:
    """One-off, used by scripts/create_razorpay_plans.py — never at request time. Plan ids
    differ between test and live, which is why they're a Setting (api/core/config.py), not
    a constant next to the prices in api/domain/plans.py."""
    return _call("POST", "/plans", json={
        "period": period, "interval": interval,
        "item": {"name": name, "amount": amount_paise, "currency": "INR",
                 "description": description},
    })


def list_invoices(subscription_id: str) -> list[dict]:
    body = _call("GET", "/invoices", params={"subscription_id": subscription_id})
    return body.get("items", [])
