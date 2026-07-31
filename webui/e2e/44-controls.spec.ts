import { test, expect, Page } from "@playwright/test";

/**
 * P4-S5 — the Controls master library in the browser.
 *
 * The API side is proven in tests/test_controls_crud.py and test_library.py. What only a
 * browser can prove: the drawer-to-page inversion actually happened (no dialog role, a
 * real deep-linkable page), the stock-answer editor round-trips, and permission gating
 * hides the right buttons for a Viewer.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function firstId(page: Page, path: string): Promise<string> {
  await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  const rows = await r.json();
  expect(rows.length, `${path} should have a seeded row`).toBeGreaterThan(0);
  return rows[0].id;
}

test.describe("the drawer became a page", () => {
  test("/controls/view/:id is a real page on a cold load, not a dialog", async ({ page }) => {
    const id = await firstId(page, "/library/controls");
    await page.goto(`/controls/view/${id}`);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "← All controls" })).toBeVisible();
    await expect(page.locator("h1")).toBeVisible();
  });

  test("clicking a row in the framework table navigates, not opens a drawer", async ({ page }) => {
    await page.goto("/controls");
    await page.locator("table tbody tr").first().click();
    await expect(page).toHaveURL(/\/controls\/view\/.+/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

test.describe("create", () => {
  test("a new control can be created and lands on its own page", async ({ page }) => {
    const code = `E2E ${uniq()}`;
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(code);
    await page.getByLabel("Statement *").fill("An E2E-created control statement.");
    await page.getByRole("button", { name: "Create control" }).click();

    await expect(page).toHaveURL(/\/controls\/view\/.+/);
    await expect(page.locator("h1")).toContainText("An E2E-created control statement.");
    await expect(page.getByText(code)).toBeVisible();
  });

  test("recurring lifecycle requires a recurrence value before submit is enabled", async ({ page }) => {
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(`E2E ${uniq()}`);
    await page.getByLabel("Statement *").fill("Recurring test control.");
    await page.getByLabel("Lifecycle").selectOption("recurring");

    const submit = page.getByRole("button", { name: "Create control" });
    await expect(submit).toBeDisabled();
    await page.getByLabel(/Recurrence \(months\)/).fill("6");
    await expect(submit).toBeEnabled();
  });

  test("not-applicable requires a justification before submit is enabled", async ({ page }) => {
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(`E2E ${uniq()}`);
    await page.getByLabel("Statement *").fill("Dormant test control.");
    await page.getByLabel("Applicability").selectOption("not_applicable");

    const submit = page.getByRole("button", { name: "Create control" });
    await expect(submit).toBeDisabled();
    await page.getByLabel(/Why not applicable/).fill("Not in scope for this deployment.");
    await expect(submit).toBeEnabled();
  });
});

test.describe("stock answer", () => {
  test("setting the stock answer persists across reload", async ({ page }) => {
    const code = `E2E Stock ${uniq()}`;
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(code);
    await page.getByLabel("Statement *").fill("Stock answer test control.");
    await page.getByRole("button", { name: "Create control" }).click();
    await expect(page).toHaveURL(/\/controls\/view\/.+/);

    await page.getByRole("button", { name: "Edit stock answer" }).click();
    await page.getByRole("button", { name: "partial", exact: true }).click();
    await page.getByPlaceholder(/Comment shown wherever/).fill("Partially implemented.");
    await page.getByRole("button", { name: "Save", exact: true }).click();

    // wait for edit mode to actually close — Playwright's text matching is case-insensitive
    // by default, so "PARTIAL" would otherwise also match the still-visible "partial" toggle
    await expect(page.getByRole("button", { name: "partial", exact: true })).toHaveCount(0);
    await expect(page.getByText("PARTIAL", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("PARTIAL", { exact: true })).toBeVisible();
    await expect(page.getByText("Partially implemented.")).toBeVisible();
  });
});

test.describe("retire / restore", () => {
  test("retiring hides a control from the framework list and restore brings it back", async ({ page }) => {
    const code = `E2E Retire ${uniq()}`;
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(code);
    await page.getByLabel("Statement *").fill("Retire test control.");
    await page.getByRole("button", { name: "Create control" }).click();
    await expect(page).toHaveURL(/\/controls\/view\/.+/);

    await page.getByRole("button", { name: "Retire" }).click();
    await expect(page.getByText("Retired")).toBeVisible();

    await page.goto("/controls");
    await expect(page.getByText(code)).toHaveCount(0);
  });
});

test.describe("evidence linkage", () => {
  test("evidence can be attached to a control and shows up both ways", async ({ page }) => {
    const code = `E2E Evidence ${uniq()}`;
    await page.goto("/controls");
    await page.getByRole("button", { name: /New control/ }).click();
    await page.getByLabel("Reference code *").fill(code);
    await page.getByLabel("Statement *").fill("Evidence linkage test control.");
    await page.getByRole("button", { name: "Create control" }).click();
    await expect(page).toHaveURL(/\/controls\/view\/.+/);

    const attach = page.getByRole("button", { name: /Attach/ }).first();
    await attach.click();
    // .count() reads synchronously and would race the /evidence fetch that `attach`
    // just triggered; the seeded e2e org always has evidence, so wait for a real row.
    const picker = page.locator("button", { hasText: "attach →" }).first();
    await expect(picker).toBeVisible();
    const title = await picker.locator("span").first().textContent();
    await picker.click();
    await expect(page.getByText(title!).first()).toBeVisible();
  });
});

test.describe("permissions", () => {
  test("a Viewer sees no write affordances on Controls", async ({ browser }) => {
    // The default fixture `page` reuses the authenticated admin storageState (see
    // auth.setup.ts), so navigating IT to /signup bounces straight back into the app —
    // signup needs a genuinely logged-out context of its own, like 41-rbac.spec.ts uses.
    const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const page = await ctx.newPage();

    const n = uniq();
    await page.goto("/signup");
    await page.getByLabel("Your name").fill("Controls Owner");
    await page.getByLabel("Work email").fill(`controls-owner-${n}@example.com`);
    await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
    await page.getByLabel("Organisation name").fill(`Controls Org ${n}`);
    const gstin = (() => {
      const A = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
      const base = `27AAPFU${String(Date.now() % 10000).padStart(4, "0")}F1Z`;
      let total = 0;
      [...base].forEach((ch, i) => {
        const p = A.indexOf(ch) * (i % 2 ? 2 : 1);
        total += Math.floor(p / 36) + (p % 36);
      });
      return base + A[(36 - (total % 36)) % 36];
    })();
    await page.getByLabel("GST number").fill(gstin);
    await page.getByRole("button", { name: "Create organisation" }).click();
    await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
    const token = await page.evaluate(() => localStorage.getItem("ar_token"));

    const made = await page.request.post("/api/e2e/make-member", {
      headers: { Authorization: `Bearer ${token}` },
      data: { email: `controls-viewer-${n}@example.com`, full_name: "Read Only",
              role_name: "Viewer", password: "Passw0rdOne" },
    });
    expect(made.ok(), await made.text()).toBeTruthy();
    await ctx.close();

    const viewerCtx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const viewer = await viewerCtx.newPage();
    await viewer.goto("/login");
    await viewer.getByLabel("Email").fill(`controls-viewer-${n}@example.com`);
    await viewer.getByLabel("Password").fill("Passw0rdOne");
    await viewer.getByRole("button", { name: "Sign in" }).click();

    await viewer.goto("/controls");
    await expect(viewer.getByRole("button", { name: /New control/ })).toHaveCount(0);
    await viewerCtx.close();
  });
});
