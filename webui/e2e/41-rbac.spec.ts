import { test, expect, Page } from "@playwright/test";

/**
 * P4-S2 — RBAC in the browser.
 *
 * The API-level enforcement is proven in tests/test_rbac.py. What matters here is the
 * WIRING: that the sidebar reflects permissions, that gated buttons disappear, and that the
 * Roles matrix actually changes what a user can do.
 */
test.use({ storageState: { cookies: [], origins: [] } });

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
let seq = 0;
const uniq = () => (Date.now() % 90000) + 1000 * ++seq;

/** Sign up a fresh org (the signer is Super Admin) and return its owner token. */
async function newOrg(page: Page) {
  const n = uniq();
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("RBAC Owner");
  await page.getByLabel("Work email").fill(`rbac-owner-${n}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(`RBAC Org ${n}`);
  await page.getByLabel("GST number").fill(gstin(n));
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
  return { n, token: await page.evaluate(() => localStorage.getItem("ar_token")) };
}

test("the owner sees every menu, including Roles", async ({ page }) => {
  await newOrg(page);
  // P4-S3 split the single "Registers" entry into five sibling menus.
  for (const name of ["Dashboard", "People", "Audits", "Controls", "Documents", "Evidence",
                      "Tasks", "Reports", "Roles",
                      "Risks", "Assets", "Data inventory", "Third parties", "Incidents"]) {
    await expect(page.getByRole("link", { name, exact: true }),
      `${name} should be visible to the owner`).toBeVisible();
  }
});

test("the Roles screen lists the built-in roles and protects them", async ({ page }) => {
  await newOrg(page);
  await page.getByRole("link", { name: "Roles", exact: true }).click();

  for (const r of ["Admin", "Editor", "Viewer"]) {
    await expect(page.getByRole("row", { name: new RegExp(`^${r}\\b`) })).toBeVisible();
  }
  // a built-in role opens read-only
  await page.getByRole("row", { name: /Viewer/ }).getByRole("button", { name: "View" }).click();
  await expect(page.getByText(/built-in role/i)).toBeVisible();
  await expect(page.locator("#role-name")).toBeDisabled();
});

test("a custom role can be created from the checkbox matrix", async ({ page }) => {
  await newOrg(page);
  await page.goto("/roles");
  await page.getByRole("button", { name: /New role/ }).click();

  await page.locator("#role-name").fill("Risk Analyst");
  await page.getByLabel("Risks View").check();
  await page.getByLabel("Risks Add").check();
  await page.getByLabel("Dashboard View").check();
  await expect(page.getByText("3 selected")).toBeVisible();
  await page.getByRole("button", { name: "Create role" }).click();

  await expect(page.getByRole("row", { name: /Risk Analyst/ })).toBeVisible();
});

test("a Viewer sees fewer menus and no write buttons", async ({ page, request, browser }) => {
  const { n, token } = await newOrg(page);

  // create a Viewer login through the API (the People invite UI lands in a later sprint)
  const email = `rbac-viewer-${n}@example.com`;
  const made = await request.post("/api/e2e/make-member", {
    headers: { Authorization: `Bearer ${token}` },
    data: { email, full_name: "Read Only", role_name: "Viewer", password: "Passw0rdOne" },
  });
  expect(made.ok(), await made.text()).toBeTruthy();

  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const viewer = await ctx.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(email);
  await viewer.getByLabel("Password").fill("Passw0rdOne");
  await viewer.getByRole("button", { name: "Sign in" }).click();

  // can see the read-only workspace — a Viewer holds <module>.view on every non-admin module…
  await expect(viewer.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
  await expect(viewer.getByRole("link", { name: "Risks", exact: true })).toBeVisible();
  // …but not the admin surfaces
  await expect(viewer.getByRole("link", { name: "Roles", exact: true })).toHaveCount(0);
  await expect(viewer.getByRole("link", { name: "Users", exact: true })).toHaveCount(0);

  // reachable, but with no create affordance
  await viewer.goto("/risks");
  await expect(viewer.getByRole("heading", { name: "Risks" })).toBeVisible();
  await expect(viewer.getByRole("button", { name: /New risk/ })).toHaveCount(0);

  // navigating straight to the roles URL yields no data (the API refuses)
  await viewer.goto("/roles");
  await expect(viewer.getByRole("row", { name: /^Admin\b/ })).toHaveCount(0);
  await ctx.close();
});
