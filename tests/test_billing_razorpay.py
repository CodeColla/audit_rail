"""Phase 10, S3a — Razorpay (Public mode). req/phase10/00-issue-19.md,
docs/phase10/01-sprint-plan.md.

Never touches the real Razorpay API: `razorpay_client`'s functions are monkeypatched to
fakes that return the same shapes the real test-mode spike captured (see
tests/fixtures/razorpay/). What IS real: the HMAC signature math, run against a fake-but-
fixed key/webhook secret, exactly as `api/domain/billing.py` implements it.
"""

import hashlib
import hmac as hmac_lib
import json
import time
import uuid

import pytest
from sqlalchemy import text as sqltext

from api.core.config import settings
from api.domain import razorpay_client
from tests.test_billing_limits import _new_org

WEBHOOK_SECRET = "fake-webhook-secret"
KEY_SECRET = "fake-key-secret"


def _fake_create_subscription(*, plan_id, quantity, total_count, notes):
    sub_id = f"sub_fake_{uuid.uuid4().hex[:10]}"
    return {"id": sub_id, "status": "created", "short_url": f"https://rzp.io/fake/{sub_id}",
           "quantity": quantity}


def _fake_cancel_subscription(subscription_id, *, at_cycle_end=False):
    return {"id": subscription_id, "status": "cancelled"}


def _fake_update_subscription_quantity(subscription_id, quantity, schedule_change_at="now"):
    return {"id": subscription_id, "status": "active", "quantity": quantity}


def _fake_list_invoices(subscription_id):
    return []


@pytest.fixture
def razorpay_configured(monkeypatch):
    for name, value in [
        ("razorpay_key_id", "rzp_test_fake"),
        ("razorpay_key_secret", KEY_SECRET),
        ("razorpay_webhook_secret", WEBHOOK_SECRET),
        ("razorpay_plan_pro_monthly", "plan_pro_m"),
        ("razorpay_plan_pro_yearly", "plan_pro_y"),
        ("razorpay_plan_addon_audits_monthly", "plan_aa_m"),
        ("razorpay_plan_addon_audits_yearly", "plan_aa_y"),
        ("razorpay_plan_addon_storage_monthly", "plan_as_m"),
        ("razorpay_plan_addon_storage_yearly", "plan_as_y"),
        ("billing_grace_days", 7),
    ]:
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(razorpay_client, "create_subscription", _fake_create_subscription)
    monkeypatch.setattr(razorpay_client, "cancel_subscription", _fake_cancel_subscription)
    monkeypatch.setattr(razorpay_client, "update_subscription_quantity",
                        _fake_update_subscription_quantity)
    monkeypatch.setattr(razorpay_client, "list_invoices", _fake_list_invoices)


def _checkout_sign(payment_id: str, subscription_id: str) -> str:
    return hmac_lib.new(KEY_SECRET.encode(), f"{payment_id}|{subscription_id}".encode(),
                        hashlib.sha256).hexdigest()


def _webhook(client, event_type: str, sub_overrides: dict, *, tenant_id: str | None = None,
            event_id: str | None = None):
    sub = {"id": "sub_missing", "status": "active", "quantity": 1,
          "current_end": int(time.time()) + 2592000,
          "notes": {"tenant_id": tenant_id} if tenant_id else {}}
    sub.update(sub_overrides)
    payload = {"event": event_type, "contains": ["subscription"],
              "payload": {"subscription": {"entity": sub}}}
    raw = json.dumps(payload).encode()
    sig = hmac_lib.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": sig,
              "X-Razorpay-Event-Id": event_id or f"evt_{uuid.uuid4().hex[:10]}"}
    return client.post("/api/billing/razorpay/webhook", content=raw, headers=headers)


def test_checkout_503_without_razorpay_configured(app_client):
    _, h = _new_org(app_client, f"rzp-unconf-{uuid.uuid4().hex[:8]}")
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    assert r.status_code == 503, r.text


def test_checkout_creates_a_pro_subscription(app_client, razorpay_configured):
    _, h = _new_org(app_client, f"rzp1-{uuid.uuid4().hex[:8]}")
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key_id"] == "rzp_test_fake"
    assert [s["item"] for s in body["subscriptions"]] == ["pro"]
    assert body["subscriptions"][0]["needs_checkout"] is True


def test_checkout_with_addons_creates_one_subscription_each(app_client, razorpay_configured):
    _, h = _new_org(app_client, f"rzp2-{uuid.uuid4().hex[:8]}")
    r = app_client.post("/api/billing/checkout", headers=h,
                        json={"cycle": "monthly", "addon_audit_packs": 2,
                              "addon_storage_packs": 1})
    assert r.status_code == 201, r.text
    items = [s["item"] for s in r.json()["subscriptions"]]
    assert items == ["pro", "addon_audits", "addon_storage"]


def test_second_checkout_while_pro_live_409s(app_client, razorpay_configured):
    _, h = _new_org(app_client, f"rzp3-{uuid.uuid4().hex[:8]}")
    app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    assert r.status_code == 409, r.text


def test_verify_rejects_a_forged_signature(app_client, razorpay_configured):
    _, h = _new_org(app_client, f"rzp4-{uuid.uuid4().hex[:8]}")
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    sub_id = r.json()["subscriptions"][0]["id"]
    r2 = app_client.post("/api/billing/verify", headers=h, json={
        "razorpay_payment_id": "pay_fake", "razorpay_subscription_id": sub_id,
        "razorpay_signature": "not-a-real-signature"})
    assert r2.status_code == 400, r2.text


def test_verify_with_correct_signature_activates_pro(app_client, razorpay_configured):
    _, h = _new_org(app_client, f"rzp5-{uuid.uuid4().hex[:8]}")
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    sub_id = r.json()["subscriptions"][0]["id"]
    sig = _checkout_sign("pay_fake", sub_id)
    r2 = app_client.post("/api/billing/verify", headers=h, json={
        "razorpay_payment_id": "pay_fake", "razorpay_subscription_id": sub_id,
        "razorpay_signature": sig})
    assert r2.status_code == 200, r2.text
    body = app_client.get("/api/billing", headers=h).json()
    assert body["plan_code"] == "pro"


def test_webhook_bad_signature_is_rejected_and_not_recorded(app_client, razorpay_configured):
    from api.core.database import engine
    payload = {"event": "subscription.activated",
              "payload": {"subscription": {"entity": {"id": "sub_x", "status": "active"}}}}
    raw = json.dumps(payload).encode()
    event_id = f"evt_bad_{uuid.uuid4().hex[:8]}"
    r = app_client.post("/api/billing/razorpay/webhook", content=raw,
                        headers={"X-Razorpay-Signature": "wrong", "X-Razorpay-Event-Id": event_id})
    assert r.status_code == 400, r.text
    with engine.connect() as c:
        n = c.execute(sqltext("SELECT count(*) FROM billing_events WHERE razorpay_event_id=:e"),
                      {"e": event_id}).scalar()
    assert n == 0


def test_webhook_replayed_event_id_is_a_noop(app_client, razorpay_configured):
    from api.core.database import engine
    j, h = _new_org(app_client, f"rzp6-{uuid.uuid4().hex[:8]}")
    tid = j["tenant_id"]
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    sub_id = r.json()["subscriptions"][0]["id"]
    event_id = f"evt_{uuid.uuid4().hex[:8]}"

    r1 = _webhook(app_client, "subscription.activated", {"id": sub_id, "status": "active"},
                 tenant_id=tid, event_id=event_id)
    assert r1.status_code == 200, r1.text
    r2 = _webhook(app_client, "subscription.activated", {"id": sub_id, "status": "active"},
                 tenant_id=tid, event_id=event_id)
    assert r2.status_code == 200, r2.text

    with engine.connect() as c:
        n = c.execute(sqltext("SELECT count(*) FROM billing_events WHERE razorpay_event_id=:e"),
                      {"e": event_id}).scalar()
    assert n == 1, "a replayed delivery must not insert a second row"


def test_webhook_unknown_subscription_is_logged_not_errored(app_client, razorpay_configured):
    """A webhook for a subscription we never created (a different product on the same
    Razorpay account, or a stray test artifact) must still 200 — Razorpay retries
    anything else forever."""
    r = _webhook(app_client, "subscription.activated", {"id": "sub_never_seen"})
    assert r.status_code == 200, r.text


def test_webhook_lifecycle_activated_then_pending_then_grace_then_free(
        app_client, razorpay_configured):
    from api.core.database import engine
    j, h = _new_org(app_client, f"rzp7-{uuid.uuid4().hex[:8]}")
    tid = j["tenant_id"]
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    sub_id = r.json()["subscriptions"][0]["id"]

    _webhook(app_client, "subscription.activated", {"id": sub_id, "status": "active"}, tenant_id=tid)
    body = app_client.get("/api/billing", headers=h).json()
    assert body["plan_code"] == "pro"
    assert body["status"] == "active"

    _webhook(app_client, "subscription.pending", {"id": sub_id, "status": "pending"}, tenant_id=tid)
    body = app_client.get("/api/billing", headers=h).json()
    assert body["status"] == "past_due"
    assert body["in_grace"] is True          # still full Pro limits inside grace
    assert body["plan_code"] == "pro"

    # simulate the grace window having fully elapsed
    with engine.begin() as c:
        c.execute(sqltext("UPDATE tenant_plans SET grace_until='2000-01-01T00:00:00Z' "
                          "WHERE tenant_id=:t"), {"t": tid})
    body = app_client.get("/api/billing", headers=h).json()
    assert body["plan_code"] == "free"
    assert body["degraded"] is True
    assert body["status"] == "past_due"      # the raw Razorpay status is unchanged —
                                              # only the DERIVED plan_code/degraded reflect grace


def test_cancelled_subscription_keeps_pro_until_period_end(app_client, razorpay_configured):
    from api.core.database import engine
    j, h = _new_org(app_client, f"rzp8-{uuid.uuid4().hex[:8]}")
    tid = j["tenant_id"]
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    sub_id = r.json()["subscriptions"][0]["id"]
    future_end = int(time.time()) + 2592000

    _webhook(app_client, "subscription.activated",
            {"id": sub_id, "status": "active", "current_end": future_end}, tenant_id=tid)
    _webhook(app_client, "subscription.cancelled",
            {"id": sub_id, "status": "cancelled", "current_end": future_end}, tenant_id=tid)

    body = app_client.get("/api/billing", headers=h).json()
    assert body["plan_code"] == "pro"        # the paid period hasn't run out yet
    assert body["status"] == "cancelled"

    with engine.begin() as c:
        c.execute(sqltext("UPDATE tenant_plans SET current_period_end='2000-01-01T00:00:00Z' "
                          "WHERE tenant_id=:t"), {"t": tid})
    body = app_client.get("/api/billing", headers=h).json()
    assert body["plan_code"] == "free"


def test_addon_purchase_raises_the_storage_limit(app_client, razorpay_configured):
    """A freshly created add-on subscription needs its OWN checkout/activation before its
    capacity counts — exactly like the initial Pro checkout does — so this fires an
    `activated` webhook for the add-on's subscription too, not just Pro's."""
    j, h = _new_org(app_client, f"rzp9-{uuid.uuid4().hex[:8]}")
    tid = j["tenant_id"]
    r = app_client.post("/api/billing/checkout", headers=h, json={"cycle": "monthly"})
    pro_sub_id = r.json()["subscriptions"][0]["id"]
    _webhook(app_client, "subscription.activated", {"id": pro_sub_id, "status": "active"}, tenant_id=tid)
    before = app_client.get("/api/billing", headers=h).json()["limits"]["storage_bytes"]

    r2 = app_client.post("/api/billing/addons", headers=h, json={"addon_storage_packs": 1})
    assert r2.status_code == 200, r2.text
    addon_sub_id = r2.json()["subscriptions"][0]["id"]

    mid = app_client.get("/api/billing", headers=h).json()["limits"]["storage_bytes"]
    assert mid == before, "an add-on's capacity must not count before ITS own checkout activates"

    _webhook(app_client, "subscription.activated", {"id": addon_sub_id, "status": "active"}, tenant_id=tid)
    after = app_client.get("/api/billing", headers=h).json()["limits"]["storage_bytes"]
    assert after == before + 25 * 1024 ** 3


def test_sync_tenant_plan_never_touches_an_enterprise_tenant(app_client, razorpay_configured):
    from api.core.database import engine
    from api.domain import billing as billing_module

    with engine.connect() as c:
        tid = c.execute(sqltext("SELECT id FROM tenants WHERE slug='kiam'")).scalar()
    with engine.begin() as c:
        c.execute(sqltext(
            "INSERT INTO razorpay_subscriptions (id,tenant_id,item,billing_cycle,quantity,"
            "status,created_at,updated_at) VALUES "
            "('sub_enterprise_defensive',:t,'pro','monthly',1,'active',"
            "'2026-10-04T00:00:00Z','2026-10-04T00:00:00Z')"), {"t": tid})
        billing_module.sync_tenant_plan(c, tid)
    with engine.connect() as c:
        plan_code = c.execute(sqltext("SELECT plan_code FROM tenant_plans WHERE tenant_id=:t"),
                              {"t": tid}).scalar()
    assert plan_code == "enterprise"
