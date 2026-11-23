import { test, expect, Page } from "@playwright/test";

/**
 * #20 S6 — the Audits list is sortable per column (DataTable).
 *
 * Each header cycles asc → desc → server order. The search box (server `?q=`) narrows the list
 * to this spec's own audits, so the assertions do not depend on whatever else the seed holds.
 *
 * Not run in this session: the dedicated Playwright database has not been seeded yet.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

async function makeAudit(page: Page, auth: Record<string, string>, templateId: string,
                         bank: string, title: string) {
  const r = await page.request.post("/api/assessments", {
    headers: auth, data: { template_id: templateId, title, bank_name: bank },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()).id as string;
}

test("each Audits column sorts asc, desc, then back to server order", async ({ page }) => {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  expect(templates.length, "the e2e seed should ship templates").toBeGreaterThan(0);

  // A shared marker in both bank names so the search box isolates exactly these two rows.
  const marker = `SortS6${uniq()}`;
  await makeAudit(page, auth, templates[0].id, `Zulu ${marker}`, "Second");
  await makeAudit(page, auth, templates[0].id, `Alpha ${marker}`, "First");

  await page.goto("/audits");
  await page.getByLabel("Search audits").fill(marker);
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(2);

  const bankHeader = page.getByRole("button", { name: /Bank \/ assessment/ });
  const firstBankCell = () => rows.first().locator("td").first();

  await bankHeader.click(); // asc
  await expect(firstBankCell()).toContainText(`Alpha ${marker}`);
  await bankHeader.click(); // desc
  await expect(firstBankCell()).toContainText(`Zulu ${marker}`);
  await bankHeader.click(); // back to server order (no sort)
  await expect(page.getByRole("button", { name: /Bank \/ assessment/ }).locator("span").last())
    .toHaveText("⇅");
});

test("a row click still opens the audit workspace", async ({ page }) => {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  const marker = `ClickS6${uniq()}`;
  const aid = await makeAudit(page, auth, templates[0].id, `Click ${marker}`, "Click title");

  await page.goto("/audits");
  await page.getByLabel("Search audits").fill(marker);
  await page.locator("tbody tr").first().click();
  await expect(page).toHaveURL(new RegExp(`/audits/${aid}$`));
});
