import { test, expect } from "@playwright/test";

/**
 * #20 S7 — the Controls framework-tab list is sortable per column (DataTable).
 *
 * The Ref sort is natural (DPDP-2 before DPDP-10), matching the server's default order from
 * S2. The crosswalk matrix is a separate table and is not covered here. A row click still opens
 * the control.
 *
 * Not run in this session: the dedicated Playwright database has not been seeded yet.
 */

// Same ordering as the API's natural sort: digit runs compare by value, not as text.
const naturalKey = (s: string) => s.replace(/\d+/g, (n) => n.padStart(12, "0"));

test("the Ref column sorts naturally, asc then desc, then back to server order", async ({ page }) => {
  await page.goto("/controls");
  const refs = () => page.locator("tbody tr td:first-child").allInnerTexts();
  const refHeader = page.getByRole("button", { name: /^Ref/ });

  const serverOrder = await refs();
  expect(serverOrder.length, "the seeded library should have controls").toBeGreaterThan(1);

  await refHeader.click(); // asc
  const asc = await refs();
  expect(asc).toEqual([...serverOrder].sort((a, b) => naturalKey(a).localeCompare(naturalKey(b))));

  await refHeader.click(); // desc
  expect(await refs()).toEqual([...asc].reverse());

  await refHeader.click(); // cleared: back to server order
  expect(await refs()).toEqual(serverOrder);
});

test("sorting by Mapped orders the points numerically", async ({ page }) => {
  await page.goto("/controls");
  const mapped = () => page.locator("tbody tr td:nth-child(5)").allInnerTexts()
    .then((xs) => xs.map((x) => parseInt(x, 10)));
  await page.getByRole("button", { name: /^Mapped/ }).click();
  const values = await mapped();
  expect(values).toEqual([...values].sort((a, b) => a - b));
});

test("a row click still opens the control", async ({ page }) => {
  await page.goto("/controls");
  await page.locator("tbody tr").first().click();
  await expect(page).toHaveURL(/\/controls\/view\//);
});
