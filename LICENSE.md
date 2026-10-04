# Enterprise licensing

This isn't an open-source license file — it's the runbook for Auditrail's **Enterprise
deployment mode**: a standalone, single-tenant deployment (the customer's own
infrastructure, or a dedicated instance on ours) with no payment gateway, entitled
instead by a signed license token you mint yourself. If you're looking for Public
Hosting's pricing and Razorpay billing, see
[`req/phase10/00-issue-19.md`](req/phase10/00-issue-19.md) and
[`docs/phase10/`](docs/phase10/) (local only, gitignored) instead — this file is just
about the licensing mechanism.

## How it works

- **One Docker image serves both deployment modes.** `DEPLOYMENT_MODE=public` (the
  default) is the shared, multi-tenant SaaS. `DEPLOYMENT_MODE=enterprise` is a standalone
  deployment with no gateway — the *only* thing that differs.
- An Enterprise deployment's entitlement — how many logins (`max_members`) and how much
  storage (`storage_gb`) — comes from a **signed JWT license token**, not a database row
  you edit by hand and not a subscription in a payment gateway.
- The token is **RS256**, asymmetric, on purpose: every deployment ships with only the
  **public** half of the keypair (`api/license_public_key.pem`, committed — it isn't
  secret). The **private** half, which is the only thing that can mint a new token, never
  leaves your own machine. This matters specifically because an Enterprise customer may
  run this deployment on *their own* infrastructure — if the key that verifies a license
  were also the key that signs one (as the app's own login tokens work, HS256), they
  could mint themselves an unlimited license. With RS256 they can't, even with full root
  access to their own servers.
- Active audits are **always unlimited** in Enterprise mode. Only `max_members` and
  storage are ever licensed.

## One-time setup (do this once, ever — not per customer)

```bash
.venv/bin/python scripts/generate_license_keypair.py --private-key ~/sr-licensing/license_private.pem
```

This writes the public half to `api/license_public_key.pem` — **commit it**, it ships in
every deployment. The private half goes wherever you point `--private-key`, which must be
**outside this repo**. Back it up somewhere safe (a password manager, an encrypted
drive) — if you lose it, every license already issued still verifies fine (that only
needs the public half), but you can't mint a *new* one without generating a fresh
keypair and re-shipping its public half to every deployment that needs to trust it.

## Issuing or renewing a license (per deal)

```bash
.venv/bin/python scripts/issue_license.py \
  --private-key ~/sr-licensing/license_private.pem \
  --customer "Acme Bank Pvt Ltd" \
  --max-members 50 \
  --storage-gb 200 \
  --valid-days 365
```

This prints a signed token to stdout. Send it to the customer's own Super Admin, who
pastes it into their deployment's **Settings → Billing** page (it shows as a "License"
screen in enterprise mode) — or, if it's a dedicated instance on your own infra, apply it
there yourself the same way. There's nothing to revoke: whichever token is *currently
applied* on their deployment is the only one that matters, so growing a deal is just
running the command again with bigger numbers and handing over the new token.

## What happens on expiry, or before any license is applied

Nothing destructive, ever. A grace period (`LICENSE_GRACE_DAYS`, default 30 days) runs
first — during it, a brand-new deployment with no license yet is simply unlimited, and an
expired license keeps its last-known numbers exactly as before. Only once that grace
period passes does usage **freeze at whatever's already there**: no new audits, logins,
or storage beyond what already exists, but nothing already in use is ever touched,
deleted, or made read-only. The deployment's own Billing page always shows which of these
states it's in (`unlicensed`, `grace`, `active`, `frozen_expired`, …) and lets an admin
paste in a renewed token at any time.

## Reference

| | |
|---|---|
| Design doc | [`docs/phase10/03-enterprise-licensing.md`](docs/phase10/03-enterprise-licensing.md) (local only, gitignored) |
| Verification logic | `api/domain/licensing.py` |
| Entitlement state machine | `api/domain/entitlements.py` → `_enterprise_limits` |
| API | `GET /api/billing`, `PUT /api/license` (`api/routers/billing.py`) |
| Settings | `DEPLOYMENT_MODE`, `LICENSE_PUBLIC_KEY_PATH`, `LICENSE_GRACE_DAYS` (`api/core/config.py`) |
| Tests | `tests/test_enterprise_licensing.py` |
