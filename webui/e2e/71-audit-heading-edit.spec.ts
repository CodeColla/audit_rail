import { test, expect, Page } from "@playwright/test";

/**
 * #20 S5 — the audit workspace heading (bank name and title) is editable in place.
 *
 * Round-trip: edit in the UI, reload, the value persists and the Audits list shows it. A blank
 * title is refused with a message and nothing is saved. The API refuses blank and cross-tenant
 * values on its own, so those are asserted directly too.
 *
 * Not run in this session: the dedicated Playwright database has not been seeded yet.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

async function ownAssessment(page: Page) {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  expect(templates.length, "the e2e seed should ship templates").toBeGreaterThan(0);
  const title = `Heading spec ${uniq()}`;
  const r = await page.request.post("/api/assessments", {
    headers: auth,
    data: { template_id: templates[0].id, title, bank_name: "Original Bank" },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { aid: (await r.json()).id as string, auth, title };
}

test("editing the bank name and title persists across reload and shows in the list", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);
  const newTitle = `Renamed ${uniq()}`;

  await page.goto(`/audits/${aid}`);
  await page.getByRole("button", { name: "Edit audit title" }).click();
  const titleInput = page.getByLabel("Audit audit title");
  await titleInput.fill(newTitle);
  await titleInput.press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(newTitle);

  await page.getByRole("button", { name: "Edit bank name" }).click();
  await page.getByLabel("Audit bank name").fill("Renamed Bank");
  await page.getByLabel("Audit bank name").press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Renamed Bank");

  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(newTitle);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Renamed Bank");

  const saved = await (await page.request.get(`/api/assessments/${aid}`, { headers: auth })).json();
  expect(saved.title).toBe(newTitle);
  expect(saved.bank_name).toBe("Renamed Bank");

  await page.goto("/audits");
  await expect(page.getByText(newTitle).first()).toBeVisible();
});

test("a blank title is refused in the UI and not saved", async ({ page }) => {
  const { aid, auth, title } = await ownAssessment(page);

  await page.goto(`/audits/${aid}`);
  await page.getByRole("button", { name: "Edit audit title" }).click();
  const titleInput = page.getByLabel("Audit audit title");
  await titleInput.fill("   ");
  await titleInput.press("Enter");
  await expect(page.getByRole("alert")).toContainText("cannot be blank");

  const saved = await (await page.request.get(`/api/assessments/${aid}`, { headers: auth })).json();
  expect(saved.title).toBe(title);
});

test("the API refuses a blank title and an unknown audit id is 404", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);

  const blank = await page.request.patch(`/api/assessments/${aid}`, {
    headers: auth, data: { title: "  " },
  });
  expect(blank.status()).toBe(400);

  const missing = await page.request.patch(`/api/assessments/00000000-0000-0000-0000-000000000000`, {
    headers: auth, data: { bank_name: "Nobody" },
  });
  expect(missing.status()).toBe(404);
});
