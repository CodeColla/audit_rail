import { test, expect, Page } from "@playwright/test";

/**
 * Phase 10, S3b — the self-serve upgrade flow. Never hits real Razorpay: `window.Razorpay`
 * is stubbed via `addInitScript` (so checkout.js is never even requested — the app's own
 * `loadCheckout()` skips the script tag entirely once `window.Razorpay` already exists),
 * and `/api/billing/checkout` + `/api/billing/verify` are stubbed with `page.route` since
 * this e2e stack has no real Razorpay keys configured (api/routers/billing.py 503s
 * without them). What those stubs can't do — actually write the subscription state a real
 * webhook would — `POST /api/e2e/billing-event` does instead; see that hook's own
 * docstring in api/routers/e2e_hooks.py.
 *
 * Each test signs up its OWN fresh org (a Free org on the shared KIAM session can't
 * exercise any of this — KIAM is grandfathered to Enterprise, unlimited, no Upgrade
 * button at all), so this file overrides the suite's shared logged-in storageState.
 */
test.use({ storageState: { cookies: [], origins: [] } });

async function signUpFreshOrg(page: Page, tag: string): Promise<string> {
  const email = `${tag}-${Date.now()}@example.com`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Billing Test");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(`${tag} Org ${Date.now()}`);
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page).toHaveURL("/");
  return email;
}

/** `window.Razorpay` resolves its `handler` almost immediately with fake-but-well-shaped
 * checkout results — the signature itself is never actually checked, since
 * /api/billing/verify is stubbed below rather than hit for real. */
async function stubRazorpayWidget(page: Page) {
  await page.addInitScript(() => {
    (window as any).Razorpay = class {
      private opts: any;
      constructor(opts: any) { this.opts = opts; }
      open() {
        setTimeout(() => this.opts.handler({
          razorpay_payment_id: "pay_e2e_fake",
          razorpay_subscription_id: this.opts.subscription_id,
          razorpay_signature: "e2e-fake-signature",
        }), 10);
      }
      on() { /* payment.failed listener — never fired in this stub */ }
    };
  });
}

async function stubCheckoutRoutes(page: Page, subscriptionId: string) {
  await page.route("**/api/billing/checkout", async (route) => {
    await route.fulfill({ json: {
      key_id: "rzp_test_e2e_fake",
      subscriptions: [{ id: subscriptionId, item: "pro", needs_checkout: true }],
      prefill: { name: "Billing Test", email: "billing-test@example.com" },
    } });
  });
  await page.route("**/api/billing/verify", async (route) => {
    await route.fulfill({ json: { ok: true } });
  });
}

/** Writes the real subscription state a webhook would have — the stubs above never touch
 * the real database, so the app's own (real) GET /billing needs this to show anything
 * other than Free. Runs in-page so it carries the same bearer token the app itself uses. */
async function fireBillingEvent(page: Page, body: Record<string, unknown>) {
  await page.evaluate(async (b) => {
    const token = localStorage.getItem("ar_token");
    const r = await fetch("/api/e2e/billing-event", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(b),
    });
    if (!r.ok) throw new Error(`billing-event hook failed: ${r.status} ${await r.text()}`);
  }, body);
}

test("Free org upgrades to Pro through the self-serve checkout flow", async ({ page }) => {
  await signUpFreshOrg(page, "upgrade");
  const subId = `sub_e2e_upgrade_${Date.now()}`;
  await stubRazorpayWidget(page);
  await stubCheckoutRoutes(page, subId);

  await page.goto("/billing");
  await expect(page.getByTestId("current-plan-name")).toHaveText("Free");

  await page.getByRole("button", { name: "Upgrade to Pro" }).click();
  await page.getByRole("button", { name: "Continue to payment" }).click();

  // The stubbed checkout resolves almost instantly, but the UI then polls the REAL
  // GET /billing — which still says Free until the hook below writes real state for
  // that SAME subscription id, exactly as a real webhook would have.
  await expect(page.getByText(/waiting for payment confirmation/i)).toBeVisible();
  await fireBillingEvent(page, { subscription_id: subId, item: "pro",
    billing_cycle: "monthly", status: "active" });

  // The polling loop will pick up the real state on its next tick (<=3s).
  await expect(page.getByTestId("current-plan-name")).toHaveText("Pro", { timeout: 10_000 });
});

test("a halted subscription shows the grace banner, not an immediate downgrade", async ({ page }) => {
  await signUpFreshOrg(page, "halted");
  const subId = `sub_e2e_halted_${Date.now()}`;

  await fireBillingEvent(page, { subscription_id: subId, item: "pro", billing_cycle: "monthly",
    status: "active" });
  await fireBillingEvent(page, { subscription_id: subId, item: "pro", billing_cycle: "monthly",
    status: "halted" });

  await page.goto("/billing");
  await expect(page.getByText(/your last payment failed/i)).toBeVisible();
  await expect(page.getByTestId("current-plan-name")).toHaveText("Pro"); // still full Pro limits, in grace
});

test("cancelling shows access lasting until the paid period ends", async ({ page }) => {
  await signUpFreshOrg(page, "cancel");
  const subId = `sub_e2e_cancel_${Date.now()}`;
  const farFuture = Math.floor(Date.now() / 1000) + 30 * 24 * 60 * 60;
  const farFutureIso = new Date(farFuture * 1000).toISOString().replace(/\.\d+Z$/, "Z");

  await fireBillingEvent(page, { subscription_id: subId, item: "pro", billing_cycle: "monthly",
    status: "active", current_end: farFutureIso });

  // The real /billing/cancel would call Razorpay for real, which this e2e stack has no
  // keys configured for — stub it the same way checkout/verify are stubbed above. It
  // doesn't touch the real database either, so the webhook-equivalent hook call after
  // it is what actually flips tenant_plans.status, same division of labour as every
  // other test in this file.
  await page.route("**/api/billing/cancel", async (route) => {
    await route.fulfill({ json: { ok: true } });
  });

  await page.goto("/billing");
  await page.getByRole("button", { name: "Cancel Pro" }).click();
  await page.getByRole("button", { name: "Cancel subscription" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();

  await fireBillingEvent(page, { subscription_id: subId, item: "pro", billing_cycle: "monthly",
    status: "cancelled", current_end: farFutureIso });
  await page.reload();

  await expect(page.getByText("Access until")).toBeVisible();
  await expect(page.getByTestId("current-plan-name")).toHaveText("Pro"); // not downgraded yet
});
