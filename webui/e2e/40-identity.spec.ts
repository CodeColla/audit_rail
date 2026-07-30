import { test, expect, Page } from "@playwright/test";

/**
 * P4-S1 — organisations, signup, password policy, org switching.
 *
 * These run in a CLEAN browser context (no storageState): signing up is by definition a
 * logged-out journey, and the app must not bounce us to /login on the way.
 */
test.use({ storageState: { cookies: [], origins: [] } });

/** A structurally valid GSTIN — 14 chars plus the real check digit. */
function gstin(seed: number): string {
  const A = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const base = `27AAPFU${String(seed % 10000).padStart(4, "0")}F1Z`;
  let total = 0;
  [...base].forEach((ch, i) => {
    const p = A.indexOf(ch) * (i % 2 ? 2 : 1);
    total += Math.floor(p / 36) + (p % 36);
  });
  return base + A[(36 - (total % 36)) % 36];
}

// Unique per test within a run: a counter guarantees no two tests collide on the GST
// number or email, which previously surfaced as a baffling timeout rather than a 409.
let seq = 0;
const uniq = () => (Date.now() % 90000) + 1000 * ++seq;

/** Signing up leaves you logged in; /login redirects to / for an authenticated session. */
async function logout(page: Page) {
  await page.evaluate(() => { localStorage.removeItem("ar_token"); localStorage.removeItem("ar_user"); });
}

async function signUp(page: Page, n: number) {
  const email = `e2e-owner-${n}@example.com`;
  const org = `E2E Org ${n}`;
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("E2E Owner");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(org);
  await page.getByLabel("GST number").fill(gstin(n));
  await page.getByRole("button", { name: "Create organisation" }).click();
  // fail here, loudly, rather than 30s later on some unrelated locator
  await expect(page.getByRole("link", { name: "Documents", exact: true }),
    "signup should land in the app").toBeVisible();
  return { email, org };
}

test("sign up creates an organisation and lands you in the app", async ({ page }) => {
  const n = uniq();
  const { org } = await signUp(page, n);

  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
  expect(page.url()).not.toContain("/signup");
  // the sidebar shows the organisation we just created
  await expect(page.getByLabel("Organisation")).toContainText(org);
});

test("a bad GST number is refused with a readable reason", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Bad GST");
  await page.getByLabel("Work email").fill(`bad-${uniq()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill("Bad GST Co");
  await page.getByLabel("GST number").fill("27AAPFU0939F1ZZ");   // wrong check digit
  await page.getByRole("button", { name: "Create organisation" }).click();

  await expect(page.getByText(/check digit/i)).toBeVisible();
  expect(page.url(), "should stay on the form").toContain("/signup");
});

test("a weak password is refused", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Weak");
  await page.getByLabel("Work email").fill(`weak-${uniq()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("alllettersonly");
  await page.getByLabel("Organisation name").fill("Weak Co");
  await page.getByLabel("GST number").fill(gstin(uniq()));
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.locator(".bg-bad-bg")).toContainText(/letters and numbers/i);
});

test("a Super Admin can run a second organisation and switch between them", async ({ page, request }) => {
  const n = uniq();
  const { org } = await signUp(page, n);

  // create the second org through the API, then reload so the session picks it up
  const tokenValue = await page.evaluate(() => localStorage.getItem("ar_token"));
  const made = await request.post("/api/auth/orgs", {
    headers: { Authorization: `Bearer ${tokenValue}` },
    data: { name: `E2E Second ${n}`, gst_number: gstin(n + 1) },
  });
  expect(made.ok(), await made.text()).toBeTruthy();

  // re-login so the org list is refreshed, then switch
  await logout(page);
  await page.goto("/login");
  await page.getByLabel("Email").fill(`e2e-owner-${n}@example.com`);
  await page.getByLabel("Password").fill("Passw0rdOne");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  const switcher = page.getByLabel("Organisation");
  await expect(switcher).toContainText("2 organisations");
  await switcher.click();
  await page.getByRole("button", { name: `E2E Second ${n}` }).click();

  await expect(page.getByLabel("Organisation")).toContainText(`E2E Second ${n}`);
  await expect(page.getByLabel("Organisation")).not.toContainText(org);
});

test("an expired password forces a change before anything else is reachable", async ({ page, request }) => {
  const n = uniq();
  await signUp(page, n);

  // age the password past the 30-day policy, then sign in again
  const tokenValue = await page.evaluate(() => localStorage.getItem("ar_token"));
  const aged = await request.post("/api/e2e/age-password", {
    headers: { Authorization: `Bearer ${tokenValue}` },
  });
  expect(aged.ok(), "test-only helper must be available").toBeTruthy();

  await logout(page);
  await page.goto("/login");
  await page.getByLabel("Email").fill(`e2e-owner-${n}@example.com`);
  await page.getByLabel("Password").fill("Passw0rdOne");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: /password has expired/i })).toBeVisible();
  // the app itself is unreachable until the password changes
  await page.goto("/documents");
  await expect(page.getByRole("heading", { name: /password has expired/i })).toBeVisible();

  await page.getByLabel("Current password").fill("Passw0rdOne");
  await page.getByLabel("New password", { exact: true }).fill("Passw0rdTwo");
  await page.getByLabel("Confirm new password").fill("Passw0rdTwo");
  await page.getByRole("button", { name: "Change password" }).click();

  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
});

test("a password cannot be changed back to a recent one", async ({ page }) => {
  const n = uniq();
  await signUp(page, n);
  await page.goto("/account/password");

  await page.getByLabel("Current password").fill("Passw0rdOne");
  await page.getByLabel("New password", { exact: true }).fill("Passw0rdTwo");
  await page.getByLabel("Confirm new password").fill("Passw0rdTwo");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  // now try to go straight back
  await page.goto("/account/password");
  await page.getByLabel("Current password").fill("Passw0rdTwo");
  await page.getByLabel("New password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Confirm new password").fill("Passw0rdOne");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText(/last 3 passwords/i)).toBeVisible();
});
