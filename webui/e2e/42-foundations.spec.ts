import { test, expect, Page } from "@playwright/test";

/**
 * P4-S3 — platform foundations in the browser.
 *
 * Three things this sprint added that only a real browser can prove:
 *   1. deep links — `/<module>/view/:id` opens the right drawer on a cold load, and the
 *      back button closes it (every drawer used to be `useState`, so nothing was linkable);
 *   2. the nav split — five register menus instead of one "Registers" tab strip, with the
 *      page title resolving for nested routes;
 *   3. vocabularies — category fields are now dropdowns fed by `lookup_values`.
 *
 * The seeded org (scripts/seed_e2e.py) has one risk, one asset, one data item, one third
 * party and one incident, so each register has a row to open.
 */

/**
 * The id of the first row in a register, read from the same API the page uses. The bearer
 * token lives in localStorage (storageState), so it has to come out of the page context —
 * a bare `request` fixture would be unauthenticated.
 */
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

test.describe("navigation", () => {
  test("the sidebar groups the five registers as separate menus", async ({ page }) => {
    await page.goto("/");
    // the old single entry is gone…
    await expect(page.getByRole("link", { name: "Registers", exact: true })).toHaveCount(0);
    // …replaced by five, under a "Registers" section heading
    for (const label of ["Risks", "Assets", "Data inventory", "Third parties", "Incidents"]) {
      await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
    // Obligations is hidden, not deleted
    await expect(page.getByRole("link", { name: "Obligations", exact: true })).toHaveCount(0);
  });

  test("/registers still resolves, for anyone holding an old link", async ({ page }) => {
    await page.goto("/registers");
    await expect(page).toHaveURL(/\/risks$/);
    await expect(page.getByRole("heading", { name: "Risks" })).toBeVisible();
  });

  test("the header title survives a nested route", async ({ page }) => {
    const id = await firstId(page, "/risks");
    await page.goto(`/risks/view/${id}`);
    // titleFor() does longest-prefix matching; before S3 this rendered blank
    await expect(page.getByTestId("page-title")).toHaveText("Risks");
  });
});

test.describe("deep links", () => {
  for (const [module, api, label] of [
    ["risks", "/risks", "Risks"],
    ["assets", "/assets", "Assets"],
    ["third-parties", "/third-parties", "Third parties"],
    ["incidents", "/incidents", "Incidents"],
  ] as const) {
    test(`/${module}/view/:id opens the drawer on a cold load`, async ({ page }) => {
      const id = await firstId(page, api);
      await page.goto(`/${module}/view/${id}`);
      await expect(page.getByRole("dialog")).toBeVisible();

      // closing returns to the list URL — the drawer is route state, not component state
      await page.keyboard.press("Escape");
      await expect(page).toHaveURL(new RegExp(`/${module}$`));
      await expect(page.getByRole("heading", { name: label })).toBeVisible();
    });
  }

  test("the back button closes a drawer opened by clicking a row", async ({ page }) => {
    const id = await firstId(page, "/risks");
    await page.goto("/risks");
    await page.getByRole("row").nth(1).click();
    await expect(page).toHaveURL(new RegExp(`/risks/view/${id}`));
    await page.goBack();
    await expect(page).toHaveURL(/\/risks$/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

test.describe("vocabularies", () => {
  test("risk category is a dropdown fed by lookup_values", async ({ page }) => {
    await page.goto("/risks");
    await page.getByRole("button", { name: /New risk/ }).click();

    const cat = page.getByLabel("Category");
    await expect(cat).toBeVisible();
    // seeded defaults from api/vocabularies.py — a <select>, not a free-text input
    await expect(cat).toHaveJSProperty("tagName", "SELECT");
    const options = await cat.locator("option").allTextContents();
    expect(options).toContain("Access control");   // seeded in api/vocabularies.py
    expect(options.length).toBeGreaterThan(3);
  });

  test("third-party category is a dropdown too", async ({ page }) => {
    await page.goto("/third-parties");
    await page.getByRole("button", { name: /New third party/ }).click();
    const cat = page.getByLabel("Category");
    await expect(cat).toHaveJSProperty("tagName", "SELECT");
    expect(await cat.locator("option").count()).toBeGreaterThan(3);
  });
});
