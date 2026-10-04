# Razorpay fixtures (Phase 10, S3a spike)

Real response shapes captured against Razorpay's TEST API on 2026-10-04, using the test
key issued for this project. No payment or card data in any of these — plan/subscription
metadata only, safe to commit. Used by `tests/test_billing_razorpay.py` (S3a) instead of
hitting the real API in CI/pytest.

- `plan_created.json` — `POST /v1/plans` response.
- `subscription_created.json` — `POST /v1/subscriptions` response (status `created`,
  before any checkout has happened).
- `subscription_quantity_update_rejected.json` — confirms the one real open question from
  the sprint plan: `PATCH /v1/subscriptions/{id}` to change `quantity` is **rejected**
  (400, `BAD_REQUEST_ERROR`) while the subscription is still `created` — it only works once
  `authenticated`/`active`. A quantity change on a never-activated subscription has to be
  cancel + recreate; a quantity change on an ALREADY-active one uses this same PATCH.
- `subscription_cancelled.json` — `POST /v1/subscriptions/{id}/cancel` response.
- `webhook_subscription_charged.json` — the documented (not live-captured — see
  01-sprint-plan.md) shape for a `subscription.charged` event, for `apply_event()`'s
  `payload["subscription"]["entity"]` parsing to be tested against before a real tunnel +
  dashboard webhook is set up.
