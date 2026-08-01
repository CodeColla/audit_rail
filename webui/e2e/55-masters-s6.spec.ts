import { test, expect } from "@playwright/test";

/**
 * P5-S6 — Masters, the vocabulary migrations, optional GST, and the header search.
 *
 * The headline test is `add a Department, then pick it on a person`: that is the exact
 * journey Sumit reported as impossible, and the reason it looked impossible was a UI lie —
 * `<input list="dept-list">` whose options were built from departments ALREADY IN USE, so it
 * read as a fixed dropdown that refused new values when typing anything always worked.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

test("add a Department in Masters, then choose it when creating a person", async ({ page }) => {
  const dept = `Field Ops ${uniq()}`;

  await page.goto("/admin");
  const card = page.locator("div").filter({ hasText: /^Department/ }).first();
  await page.getByPlaceholder("Add to department…").fill(dept);
  await page.getByPlaceholder("Add to department…").press("Enter");
  await expect(card.getByText(dept)).toBeVisible();

  // …and it is offered immediately on the People form, with no reload
  await page.goto("/people");
  await page.getByRole("button", { name: /Add person/ }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("Full name *").fill(`Person ${uniq()}`);
  await modal.getByLabel("Department").selectOption(dept);
  await expect(modal.getByLabel("Department")).toHaveValue(dept);
});

test("hiding a value keeps it off new forms without rewriting existing records", async ({ page }) => {
  const value = `Temp Cat ${uniq()}`;
  await page.goto("/admin");
  await page.getByPlaceholder("Add to risk category…").fill(value);
  await page.getByPlaceholder("Add to risk category…").press("Enter");

  const row = page.locator("div").filter({ hasText: value }).last();
  await row.getByRole("button", { name: "Hide" }).click();
  await expect(page.getByText(value).first()).toBeVisible();     // still listed in Masters
  await expect(page.getByText("hidden").first()).toBeVisible();

  // but a form no longer offers it
  await page.goto("/risks");
  await page.getByRole("button", { name: /New risk/ }).click();
  const options = await page.getByRole("dialog").locator("select").first().locator("option").allTextContents();
  expect(options).not.toContain(value);
});

test("every vocabulary the app uses is manageable here", async ({ page }) => {
  await page.goto("/admin");
  for (const label of ["Department", "Position / job title", "Evidence type",
                       "Obligation area", "Regulator", "Risk category",
                       "Incident category", "Asset subtype"]) {
    await expect(page.getByPlaceholder(`Add to ${label.toLowerCase()}…`),
      `${label} should be manageable`).toBeVisible();
  }
});

test("the header search actually searches", async ({ page }) => {
  const tag = `hs${uniq()}`;
  const token = await (async () => { await page.goto("/"); 
    return page.evaluate(() => localStorage.getItem("ar_token")); })();
  await page.request.post("/api/risks", {
    headers: { Authorization: `Bearer ${token}` }, data: { title: `${tag} searchable risk` } });

  await page.goto("/documents");
  // it was a decorative <input> with no handler until P5-S6
  await page.getByLabel("Search everything").fill(tag);
  const panel = page.getByText(`${tag} searchable risk`);
  await expect(panel).toBeVisible({ timeout: 10_000 });
  await panel.click();
  await expect(page).toHaveURL(/\/risks/);
});

test("signup works with no GST at all", async ({ browser }) => {
  // GST was required and checksum-verified, which blocked anyone without one to hand. Note
  // this needs a SIGNED-OUT context: every other spec reuses the shared storageState.
  const fresh = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const p = await fresh.newPage();
  await p.goto("/signup");
  const tag = uniq();
  await p.getByLabel("Your name").fill("No GST Founder");
  await p.getByLabel("Work email").fill(`nogst-${tag}@example.com`);
  await p.getByLabel("Password").fill("Passw0rdOne");
  await p.getByLabel(/Organisation/).fill(`No GST Co ${tag}`);
  await p.getByRole("button", { name: /Create/ }).click();
  await expect(p.getByRole("link", { name: "Documents" })).toBeVisible({ timeout: 15_000 });
  await fresh.close();
});
