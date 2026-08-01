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

/**
 * A GSTIN with a deliberately WRONG check digit, unique per run. Both properties matter: it
 * must be malformed to prove S6 no longer validates, and unique because the number is still
 * enforced as unique — a hardcoded one is claimed by the first run and makes the spec fail
 * on every run after.
 */
function badGstin(seed: number): string {
  const good = gstin(seed);
  const A = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const wrong = A[(A.indexOf(good[14]) + 1) % 36];   // any other char is, by definition, wrong
  return good.slice(0, 14) + wrong;
}

test("an unusual GST number is accepted — but a duplicate is still refused", async ({ page }) => {
  /**
   * P5-S6 reversed this deliberately. GST used to be required and checksum-validated, so a
   * wrong check digit blocked signup entirely; this spec asserted that block. But the format
   * is a tax-registration detail that changes, varies by entity, and often is not to hand at
   * signup — validating it turned a nice-to-have into a wall, which is what Sumit reported.
   *
   * Uniqueness is the part worth keeping, so that is what is asserted now: the same GST
   * cannot register two organisations.
   */
  const shared = badGstin(uniq());
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Odd GST");
  await page.getByLabel("Work email").fill(`odd-${uniq()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(`Odd GST Co ${uniq()}`);
  await page.getByLabel("GST number").fill(shared);
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  // …now a second organisation claiming the same number is turned away, with the reason.
  const fresh = await page.context().browser()!.newContext(
    { storageState: { cookies: [], origins: [] } });
  const p2 = await fresh.newPage();
  await p2.goto("/signup");
  await p2.getByLabel("Your name").fill("Dupe GST");
  await p2.getByLabel("Work email").fill(`dupe-${uniq()}@example.com`);
  await p2.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await p2.getByLabel("Organisation name").fill(`Dupe GST Co ${uniq()}`);
  await p2.getByLabel("GST number").fill(shared);
  await p2.getByRole("button", { name: "Create organisation" }).click();
  await expect(p2.locator(".bg-bad-bg")).toContainText(/already/i);
  expect(p2.url(), "should stay on the form").toContain("/signup");
  await fresh.close();
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
