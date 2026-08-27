import { test, expect } from "@playwright/test";

/**
 * Issue #13 Phase 0 — quick wins.
 *
 * Three independent, low-risk checks: the "Audit Rail" -> "Auditrail" rename reads correctly
 * everywhere it matters, the new PENDING risk treatment is selectable and persists, and the
 * long-standing People email-uniqueness guard (already correct in the code, per issue #13's
 * investigation) hasn't regressed.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

test("the portal reads Auditrail, not Audit Rail", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/Auditrail/);
  await expect(page).not.toHaveTitle(/Audit Rail/);

  // Shell.tsx renders the Wordmark in the authenticated app's sidebar.
  await expect(page.getByText("Auditrail", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Audit Rail", { exact: true })).toHaveCount(0);
});

test("a new risk can be created with treatment Pending", async ({ page }) => {
  await page.goto("/risks");
  await page.getByRole("button", { name: /New risk/i }).click();

  const tag = uniq();
  await page.getByLabel(/Title/).fill(`Phase 0 pending-treatment risk ${tag}`);
  await page.getByLabel("Treatment").selectOption({ label: "Pending" });
  await page.getByRole("button", { name: "Create risk" }).click();

  await expect(page.getByText(`Phase 0 pending-treatment risk ${tag}`)).toBeVisible();
  await page.getByText(`Phase 0 pending-treatment risk ${tag}`).click();
  await expect(page.getByRole("dialog").getByText("Pending", { exact: true })).toBeVisible();
});

test("adding a person with a duplicate email is rejected with a friendly message", async ({ page }) => {
  const tag = uniq();
  const email = `dup-${tag}@example.com`;

  await page.goto("/people");
  await page.getByRole("button", { name: "Add person" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Full name/).fill(`Dup Person ${tag}`);
  await dialog.getByLabel(/Email/).fill(email);
  await dialog.getByRole("button", { name: "Add person" }).click();
  await expect(page.getByText(`Dup Person ${tag}`)).toBeVisible();

  // Same email, different case — the DB's `email_addr` domain lowercases both sides.
  await page.getByRole("button", { name: "Add person" }).click();
  await dialog.getByLabel(/Full name/).fill(`Dup Person Two ${tag}`);
  await dialog.getByLabel(/Email/).fill(email.toUpperCase());
  await dialog.getByRole("button", { name: "Add person" }).click();

  await expect(dialog.getByRole("alert")).toContainText(/already/i);
  await expect(page.getByText(`Dup Person Two ${tag}`)).toHaveCount(0);
});
