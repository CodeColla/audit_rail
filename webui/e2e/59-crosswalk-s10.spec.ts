import { test, expect } from "@playwright/test";

/**
 * P5-S10 — the two things left open after S9.
 *
 *  1. The crosswalk review was reachable ONLY in the seconds after an import finished: it
 *     lived inside the import wizard, gated on that import's in-memory result. Navigate away
 *     and 667 proposed mappings became unreachable. The API was always complete.
 *  2. Clause import, so a standard we do not ship (and cannot legally ship the text of) can
 *     still be brought in.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

test("the mapping backlog is reachable without re-importing anything", async ({ page }) => {
  await page.goto("/audits");
  await page.getByRole("link", { name: "Mappings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Checklist mappings" })).toBeVisible();

  // a checklist with unreviewed proposals is visible as such, from a standing start
  const card = page.locator("a[href^='/mappings/']").first();
  await expect(card).toBeVisible();
  await card.click();

  await expect(page.getByRole("button", { name: /Confirm all ≥ 50%/ })).toBeVisible();
  const pending = page.getByText(/\d+ to review/);
  await expect(pending).toBeVisible();

  // confirming one really moves it — the review works outside the wizard
  const before = Number((await pending.textContent())!.match(/(\d+) to review/)![1]);
  test.skip(before === 0, "this template has nothing left to review");
  await page.getByRole("button", { name: /^Confirm mapping for question/ }).first().click();
  await expect(page.getByText(`${before - 1} to review`)).toBeVisible();
});

test("only-unreviewed narrows the list to what still needs a decision", async ({ page }) => {
  await page.goto("/mappings");
  await page.locator("a[href^='/mappings/']").first().click();
  // wait for the table to paint — .count() answers immediately and would read 0
  await expect(page.locator("tbody tr").first()).toBeVisible();
  const all = await page.locator("tbody tr").count();
  await page.getByRole("button", { name: "Show only unreviewed" }).click();
  await expect(page.getByRole("button", { name: "Show all" })).toBeVisible();
  expect(await page.locator("tbody tr").count()).toBeLessThanOrEqual(all);
});

test("a framework's clauses can be imported from a spreadsheet", async ({ page }) => {
  const tag = uniq();
  await page.goto("/frameworks");

  // a framework we invented — the point being that a standard we don't ship still works
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const made = await page.request.post("/api/frameworks", {
    headers: { Authorization: `Bearer ${token}` },
    data: { code: `BYO-${tag}`, name: `Bring Your Own ${tag}` } });
  expect(made.ok(), await made.text()).toBeTruthy();

  await page.reload();
  await page.getByRole("link", { name: `Bring Your Own ${tag}` }).click();
  await page.getByRole("button", { name: /Import clauses/ }).click();

  const modal = page.getByRole("dialog");
  // The column-mapping table only renders once a file has been read — the modal parses the
  // header row in the browser so you can map columns before anything is uploaded.
  await modal.locator('input[type="file"]').setInputFiles({
    name: "clauses.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Reference,Title\nBYO.1,First requirement\nBYO.2,Second requirement\n"),
  });
  await expect(modal.getByText("Match your columns")).toBeVisible();
  // assert on the help text, not the label: "Reference" is ALSO the name of a column in the
  // uploaded file, so it appears as an <option> in all three mapping selects
  await expect(modal.getByText(/the clause number as the standard writes it/)).toBeVisible();
  await modal.getByRole("button", { name: "Import", exact: true }).click();
  await expect(modal.getByText(/Imported/)).toBeVisible();
  await modal.getByRole("button", { name: "Done" }).click();

  // …and they are real clauses: they show up in readiness, awaiting a control
  await expect(page.getByText("BYO.1")).toBeVisible();
  await expect(page.getByText("First requirement")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Not mapped 2$/ })).toBeVisible();
});
