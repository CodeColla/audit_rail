# Pricing module: Free / Pro / Enterprise, Razorpay billing, landing page update

## Why
Right now Auditrail has no self-serve way to pay. The landing page offers a 30-day trial and then a "talk to sales" Enterprise plan. The backend has no plans, quotas or billing.

We want:
- a **free plan** anyone can sign up for,
- a **paid plan** people can buy themselves, sized by **number of audits** and **storage**,
- **Enterprise** kept as custom/sales-led.

The landing page must show the new pricing and point at the live app at **https://app.auditrail.srassociates.co/**.

### Business details (used on invoices, the Contact page and Razorpay KYC)
| | |
|---|---|
| Legal name | **SR Associates** (Indian sole proprietorship) |
| Address | Sai Niwas, Sunita Nagar, Vadgaonsheri, Pune – 411014, Maharashtra |
| Sales / support email | **hello@srassociates.co** |
| Phone | **Pending.** The owner will provide it. Until then the Contact page uses the placeholder `+91 00000 00000`, read from `VITE_CONTACT_PHONE` so it can be swapped without code changes. |
| GSTIN | **Not registered yet.** See "GST" below. |

---

## 1. Pricing decision

### Plans
Billing is **per organisation (tenant)**. All prices are in INR.

**GST.** SR Associates is not GST-registered yet, so **no GST is charged** and the prices below are the final amount the customer pays.
- Registration becomes **mandatory once turnover crosses ₹20 lakh a year** (the limit for services in Maharashtra). From then on, 18% GST is added on top: Pro becomes ₹2,499 + GST.
- Some corporate or bank customers may ask for a GSTIN so they can claim input tax credit. Registering voluntarily earlier is allowed if a deal needs it.
- The code must not hard-code "no GST". Store a `gst_rate` (0 for now), so switching it on is a config change, not a rebuild.

| | **Free** | **Pro** | **Enterprise** |
|---|---|---|---|
| Price | ₹0, forever, no card needed | **₹2,499 / org / month**, or **₹24,990 / year** (2 months free) | Custom, via sales |
| Active audits | **1** | **10** | Contract |
| Evidence & document storage | **500 MB** | **25 GB** | Contract |
| Team members | 3 | Unlimited | Unlimited |
| Guest auditors (bank / external) | Unlimited, never counted | Unlimited, never counted | Unlimited |
| Features | All features | All features | All features + SSO, dedicated CSM, custom contract / DPA |
| Support | Email (best effort) | Email, 1 business day | Dedicated CSM |

**Pro add-ons** (on top of the plan, billed on the same cycle):
| Add-on | Monthly | Yearly |
|---|---|---|
| +5 active audits | ₹999 | ₹9,990 |
| +25 GB storage | ₹499 | ₹4,990 |

### Rules behind the numbers
- **"Active audit" = an audit whose status is not `closed`.** Closed audits stay fully readable and exportable on every plan, so the history you reuse answers from is never locked away. This matches the "Active audits" wording the landing page already uses.
  - Loophole to close: a closed audit can be reopened through `PATCH /assessments/{id}` (`api/routers/assessments.py:193`). **Reopening must check the same cap as creating.**
- **Storage = `SUM(files.size_bytes)` for the tenant.** This counts every uploaded file: evidence, policies, documents, templates, register attachments and the logo.
- **Guests are never counted.** They are the bank's or the auditor's people. Charging for them would make the people we want to impress pay.
- **Free is a real free tier, not a trial.** One live audit is enough to try the product on a real questionnaire. Running audits continuously is the moment someone pays.
- **Why ₹2,499?** Vanta, Drata and Sprinto start around $7k–12k per year and are aimed at SOC 2 startups. ₹24,990/yr (about $300) is an easy "just buy it" decision for an Indian SME or a CA/consulting firm that gets audited several times a year. We can raise prices later; lowering them is harder.
- **Going over a limit never deletes anything.**
  - At the cap, creating a new audit, reopening a closed one, or uploading a file is blocked. The user sees an "Upgrade / buy add-on" prompt (HTTP **402** with code `plan_limit`).
  - Everything else keeps working: reading, editing, exporting, closing audits, deleting files.
- **Lapsed payment.**
  - The subscription goes `past_due`, followed by a **7-day grace period** at full Pro limits.
  - After that the org drops to Free limits. Nothing is deleted, and the org is over its cap until it pays or cleans up.
- **Existing orgs** (created before this ships) are moved to Enterprise/grandfathered with no limits, so nobody in production gets locked out on deploy day.

---

## 2. Payment gateway decision → **Razorpay**

| Option | Verdict |
|---|---|
| **Razorpay** ✅ | India-first. INR, UPI, cards, netbanking. **Subscriptions** with UPI Autopay and card e-mandates. Generates invoices, with GST once we register. Onboards sole proprietorships **without a GSTIN** (PAN + bank account + a business proof). Python SDK and webhooks. Fees about 2% + GST on domestic payments; subscriptions add roughly 1% on top. International cards are possible later (3% + GST, needs activation). |
| Cashfree | Similar features and pricing, a fine fallback. Razorpay's subscriptions and docs are more mature and more widely known. |
| Stripe | **Invite-only for Indian businesses since May 2024**, so we can't count on getting an account. |
| Paddle / Dodo Payments (merchant of record) | Worth considering **later** if we sell to global customers in USD, because they handle foreign tax for us. They charge about 4–5% + a fixed fee per transaction, which is too expensive for INR customers. |

**The Free plan never touches the gateway.** Signup stays exactly as it is today, with no card required. Razorpay only comes in when someone clicks **Upgrade to Pro**.

### How the Razorpay flow works
1. An org admin opens **Settings → Billing** and clicks **Upgrade**. The API creates a Razorpay **Subscription** for the right Plan ID (Pro monthly or yearly, plus add-ons) and returns `subscription_id`.
2. The frontend opens **Razorpay Checkout** (`checkout.js`) with that ID. The customer pays with UPI Autopay or a card mandate.
3. When checkout succeeds, the browser posts Razorpay's signed response to `POST /api/billing/verify`.
   - The server checks its HMAC with our key secret.
   - If it's valid, Pro switches on immediately. An unsigned browser callback is never trusted.
4. Razorpay also calls our **webhook** `POST /api/billing/razorpay/webhook`. This is the source of truth and the only path for renewals, failed payments and cancellations.
   - Verify the `X-Razorpay-Signature` HMAC using the webhook secret.
   - Store each event once, using the event id for idempotency.
   - Handle `subscription.activated`, `subscription.charged`, `subscription.pending`, `subscription.halted`, `subscription.cancelled` and `payment.failed`.
5. Razorpay generates the invoices in SR Associates' name.
   - For now these are plain invoices with no GST line.
   - Once SR Associates registers for GST, add our GSTIN in Razorpay. The customer org's existing `tenants.gst_number` is then passed through so B2B customers get a GST invoice they can claim input credit on.

Note: under RBI e-mandate rules, recurring debits **above ₹15,000** need the customer to approve each charge. The yearly plan (₹24,990) falls under this. Razorpay sends the approval request; the UI only needs to say so.

### What Razorpay needs before we can go live (non-code, owner action)
- [ ] Razorpay account for **SR Associates (sole proprietorship)**. KYC needs:
  - the proprietor's PAN and Aadhaar,
  - a bank account (a current account in the business name is preferred),
  - one business-existence proof, such as **Udyam registration** (free, online) or a **Shop & Establishment licence** (Pune Municipal Corporation).
  - A GSTIN is **not** required.
- [ ] Public pages on the website, which Razorpay checks during activation: **Pricing**, **Terms of Service**, **Privacy Policy**, **Refund & Cancellation Policy**, **Contact Us**.
  - Contact Us must show the address and email above, plus a phone number.
- [ ] Create the Plans in the Razorpay dashboard (Pro monthly, Pro yearly, both add-ons, each monthly and yearly). Test mode first, then live.

---

## 3. Landing page changes (`landingpage/`)

The landing page is its own Vite app and container (port 8081). `VITE_*` values are **baked in at build time**, so the landing image must be **rebuilt**, not just restarted.

- [ ] **Domain**
  - `src/lib/env.ts`: add `APP_URL = import.meta.env.VITE_APP_URL ?? "https://app.auditrail.srassociates.co"`. Build `SIGNUP_URL = ${APP_URL}/signup` and `LOGIN_URL = ${APP_URL}/login` from it.
  - Replace the `app.auditrail.example.com` fallback.
  - Change the `SALES_EMAIL` fallback in `env.ts` and `VITE_SALES_EMAIL` in `.env` and `.env.example` to **`hello@srassociates.co`**.
  - Today the live page shows `sales@auditrail.example.com`, because `.env` never set it.
- [ ] **Nav**: add a **Log in** link (`LOGIN_URL`) next to the sign-up button. There is currently no way to reach the app's login from the landing page.
- [ ] **Pricing section** (`src/components/Pricing.tsx`): replace "Trial + Enterprise" with three cards, **Free / Pro / Enterprise**.
  - Monthly/yearly toggle.
  - Show prices as the final amount, with no "+ GST" label for now. Read the GST note from one constant so it can be switched on later.
  - Each card lists the limits above (active audits, storage, members, guests).
  - Remove the now-wrong copy: "No self-serve rate card", "The only paid plan", "30 days" and "After day 30: read-only…".
  - Pro is the highlighted card.
  - CTAs: Free → "Start free" (`SIGNUP_URL`); Pro → "Start free, upgrade anytime" (`SIGNUP_URL?plan=pro`); Enterprise → "Talk to sales" (mailto, unchanged).
  - A short add-ons line under the cards.
  - Small FAQ: What counts as an active audit? What happens if I hit a limit? Are guest auditors free? Can I cancel? Do I get an invoice?
- [ ] **Hero / CTA buttons** (`Hero.tsx`, `CTASection.tsx`, `Nav.tsx`) currently say "Get started". Optionally change them to "Start free". No "trial" wording exists outside `Pricing.tsx`.
- [ ] **Footer**: add links to Terms, Privacy, Refund & Cancellation and Contact.
  - These are new static pages in the landing app; Razorpay requires them.
  - The pages use the business details above. The footer also shows "© SR Associates" and the contact email.
  - The footer comment saying "no such pages exist" goes away.
  - The legal text is drafted in S1, and the owner reviews it before publishing.
- [ ] Rebuild and redeploy the landing image.

---

## 4. Backend and app changes

The full file-by-file plan is in `docs/phase10/01-sprint-plan.md`. The key design points:

### Data
- **Plans live in code** (`api/domain/plans.py`): limits, prices and add-on sizes. This is the same approach as the permissions catalogue, so a price change is a deploy, not a migration. `GET /api/billing/plans` serves them to the UI.
- **New tables** (in `db/schema.sql`, plus hand-run SQL on dev, `audit_rail_e2e` and, separately, prod; there is no Alembic):
  - `tenant_plans`: one row per org, holding the *effective* plan.
    - Columns: `plan_code`, `status` (`active`/`past_due`/`cancelled`), cycle, period end, `grace_until`, add-on pack counts, and Enterprise override limits.
    - Existing orgs are backfilled as `enterprise`. New orgs get `free` in `_create_org`.
  - `razorpay_subscriptions`: mirrors Razorpay.
    - There is **one subscription per purchasable item**: Pro, "+5 audits" × qty and "+25 GB" × qty. Razorpay's own add-ons are one-time charges only.
    - This is confirmed in a test-mode spike at the start of S3.
  - `billing_events`: the raw webhook log, keyed by Razorpay's event id, so a replayed event is a no-op.

### Limits (enforced on the server, HTTP 402 `plan_limit`)
- **Audits:** creating an audit, reopening a closed one, and **importing a checklist**. A new audit is always created straight after an import, so checking only at creation would leave an orphan checklist.
- **Storage:** checked before every *user* upload (evidence, policies, document images, templates, register attachments, logo). The PDF generated when a document is published counts toward usage but is **never blocked**, because publishing must not break over a limit the user can't see.
- **Members** means logins (`tenant_members`), not `people` records. Staff who only sign policy attestations by magic link are never counted.
- **Grace periods need no scheduled job.** Limits are worked out on every read by comparing `grace_until` with the current time.
- **An org with no plan row is treated as unlimited, and a warning is logged.** We never lock out a legacy customer because a row is missing.
- **`scripts/set_plan.py`** sets up Enterprise deals and manual fixes. There is no admin UI this phase.

### Billing API (`api/routers/billing.py`)
- `GET /billing`: plan, limits, usage, status.
- `GET /billing/plans`
- `POST /billing/checkout`
- `POST /billing/verify`
- `POST /billing/addons`
- `POST /billing/cancel`
- `GET /billing/invoices`
- `POST /billing/razorpay/webhook`: public, HMAC-verified, registered next to `signing`.

Other details:
- New permission module `billing` (view/edit), for Admin and the Super Admin only.
- Razorpay is called with the already-installed `httpx`, so there is no new SDK dependency.
- Keys and plan ids go in `api.env` and are never committed.
- `gst_rate = 0` is configurable.
- Every billing event is logged with `loguru`; secrets are never logged.

### Web UI
- A **Billing** page in the sidebar footer, next to Admin:
  - plan, usage bars and renewal date;
  - Upgrade through Razorpay Checkout;
  - add-ons, cancel (states the refund policy) and invoices.
- A global **plan-limit dialog** on any 402. Admins see "Upgrade"; everyone else sees "ask your admin".
- A **banner** for failed payments or the grace period.
- `/signup?plan=pro` opens the upgrade straight after signup.

---

## 5. Sprints
The detailed plan is in `docs/phase10/01-sprint-plan.md` (local only, gitignored).

| Sprint | Scope | Blocked by |
|---|---|---|
| **S1: Landing page** | Domain/env, Log in link, 3-tier pricing with a monthly/yearly toggle, FAQ, legal pages (Terms, Privacy, Refund, Contact), footer | — (phone is a placeholder) |
| **S2a: Limits (backend)** | Plans in code, `tenant_plans`, entitlements, all enforcement points, `GET /billing`, permissions, `set_plan.py` | — |
| **S2b: Limits (UI)** | Billing page (read-only), 402 dialog, import pre-check | S2a |
| **S3a: Razorpay (backend, test mode)** | Spike, client, checkout/verify/webhook/cancel/add-ons/invoices, grace logic | S2a and Razorpay **test** keys (available right after Razorpay signup, before KYC) |
| **S3b: Razorpay (UI)** | Checkout flow, add-ons, cancel, invoices, grace banner, `?plan=pro` | S3a |
| **S4: Go live** | Live plans and webhook, prod migration (hand-run after `pg_dump`), real ₹ smoke test + refund | Razorpay KYC approved |

S1 and S2a can start in parallel straight away.

## 6. Tests
- **pytest**:
  - Each limit blocks at the cap and allows below it.
  - The reopen-a-closed-audit check.
  - Guests are not counted.
  - Storage is summed per tenant only, with no cross-tenant leak.
  - Webhook: a bad signature is rejected, and a duplicate event id is a no-op.
  - The `activated → halted → grace → free` transitions.
  - New signup gets Free; existing tenants are grandfathered.
- **Playwright**:
  - A Free org hits the 2nd-audit cap and sees the upgrade dialog.
  - The Billing page shows usage.
  - Upgrade with Razorpay **test mode** (mock the webhook in the e2e stack).
- **Manual**: landing links open `app.auditrail.srassociates.co/signup` and `/login` in the production build.

## 7. Decisions

### Locked
- [x] Prices: Pro ₹2,499 a month or ₹24,990 a year. Add-ons ₹999 (+5 audits) and ₹499 (+25 GB).
- [x] Free plan: **1 active audit, 500 MB**, 3 members.
- [x] Gateway: Razorpay. Currency: INR only.
- [x] Seller: SR Associates (sole proprietorship), Pune. Not GST-registered, so no GST is charged for now.
- [x] Sales / support email: hello@srassociates.co.
- [x] CORS: no change needed.
  - The app calls its API on the same address (`webui` uses a relative `/api` that nginx forwards to the backend), so browser cross-origin rules don't apply.
  - Razorpay's webhook is server-to-server and isn't affected either.
  - The stale `audit.iam-kiam.com` value in `_docker/env/api.env` can be tidied, but it is not a blocker.

- [x] **Refund & Cancellation policy:**
  - Cancel anytime, and access continues until the end of the paid period.
  - Monthly payments are non-refundable.
  - Yearly payments get a full refund if requested within 7 days of the first payment.
  - Refunds go back to the original payment method within 5–7 working days.

### Still open
- [ ] **Phone number** for the Contact page (Razorpay requires one). A placeholder is used until the owner provides it.
- [ ] **Razorpay onboarding** (owner, running in parallel): KYC and a business proof (Udyam or Shop & Establishment), plus the payout bank account. This blocks S4 (go-live) only. S1–S3 can be built in test mode before it's done.
- [ ] **Landing page domain:** where the marketing site itself is hosted, for example `auditrail.srassociates.co`. Razorpay asks for this website URL. *Default until answered:* the code doesn't depend on it; only deployment does.
- [ ] Pro 14-day free trial on top of Free? *Default: no.* Free already covers trying it out.
- [ ] International (USD) in the next 6 months? *Default: no, INR only.* Revisit as a later phase.

## Out of scope
- Enterprise self-serve (stays sales-led)
- Usage-based metering beyond audits, storage and members
- Coupons and discounts
- Multi-currency
- Seat-based pricing
