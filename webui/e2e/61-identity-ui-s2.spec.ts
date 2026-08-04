import { test, expect } from "@playwright/test";

/**
 * P6 — organisation identity and the account menu.
 *
 * Sumit, before public launch: *"the org name on the top Right does not hilight a lot, maybe
 * an Org Icon/Photo provision we should give… from the user icon to sizing."*
 */

// A 1×1 PNG. The bytes matter (the API sniffs magic bytes, not the filename); the pixels do not.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64");

test("the header carries the organisation, and the page title is no longer repeated", async ({ page }) => {
  await page.goto("/controls");
  const header = page.locator("header");
  await expect(header).toContainText("KIAM INTL");
  // it used to read "ORG / Controls", then "Controls" beneath it, then the page's own
  // PageHead — the word appeared three times above the fold
  await expect(header.getByTestId("page-title")).toHaveText("Controls");
  expect((await header.innerText()).match(/Controls/g) ?? []).toHaveLength(1);
});

test("the account menu replaces the sidebar sign-out and can reach the password page", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Account" }).click();
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: /Sign out/ })).toBeVisible();

  // Change password had a route but nothing linked to it from anywhere in the app
  await menu.getByRole("menuitem", { name: /Change password/ }).click();
  await expect(page).toHaveURL(/\/account\/password/);

  // Escape closes it rather than trapping the user
  await page.goto("/");
  await page.getByRole("button", { name: "Account" }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
});

test("an uploaded logo replaces the initials tile, and removing it restores them", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Masters" })).toBeVisible();

  const header = page.locator("header");
  await expect(header.locator("img")).toHaveCount(0);      // initials to begin with

  await page.locator('input[type="file"]').first().setInputFiles({
    name: "logo.png", mimeType: "image/png", buffer: PNG });

  // the Organisation card shows it immediately, without a reload
  await expect(page.locator("main img, img").first()).toBeVisible({ timeout: 10_000 });
  await page.reload();
  await expect(header.locator("img")).toHaveCount(1);
  await expect(page.locator("aside img")).toHaveCount(1);  // and in the sidebar tile

  await page.getByRole("button", { name: "Remove" }).click();
  await page.reload();
  await expect(header.locator("img")).toHaveCount(0);
});

test("an SVG is refused with a reason a person can act on", async ({ page }) => {
  // An SVG is executable markup and this image renders inside the app on every screen.
  await page.goto("/admin");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "logo.svg", mimeType: "image/svg+xml",
    buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
  });
  await expect(page.getByRole("alert")).toContainText(/SVG/i);
  await expect(page.locator("header img")).toHaveCount(0);
});

test("the organisation switcher still works — its accessible name is load-bearing", async ({ page }) => {
  // Four assertions in 40-identity.spec.ts target getByLabel("Organisation"). The P6 header
  // rework must not have moved or renamed it.
  await page.goto("/");
  await expect(page.getByLabel("Organisation")).toContainText("KIAM INTL");
});
