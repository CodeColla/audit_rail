import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 3 — Documents: hard delete (guarded), editable Details tab, and the new
 * "Linked Controls" / "Linked Audit Point" sections.
 *
 * Delete is deliberately a DIFFERENT policy from Evidence's (Phase 4): Evidence always
 * succeeds and nulls out whatever pointed at it, but a document can BE a Statement of
 * Applicability's cited artifact, an access-review campaign's output, or the org's own NDA —
 * compliance records in their own right. The blocked-delete test below uses a test-only hook
 * (`POST /api/e2e/set-nda-document`, gated behind E2E_TEST_HOOKS like every other hook in
 * this suite) since Trust Center itself has no product UI/API yet to reach that state from.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

async function auth(page: Page) {
  const tok = await token(page);
  return { Authorization: `Bearer ${tok}` };
}

async function ownDocument(page: Page, a: Record<string, string>, title: string) {
  const people = await (await page.request.get("/api/people", { headers: a })).json();
  expect(people.length).toBeGreaterThan(0);
  const r = await page.request.post("/api/documents", {
    headers: a, data: { title, owner_person_id: people[0].id } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { id: (await r.json()).id, ownerId: people[0].id, people };
}

test("deleting a document set as the org's NDA is blocked, naming the reason", async ({ page }) => {
  const a = await auth(page);
  const title = `NDA-blocked doc ${uniq()}`;
  const { id } = await ownDocument(page, a, title);
  const nda = await page.request.post("/api/e2e/set-nda-document", { headers: a, data: { document_id: id } });
  expect(nda.ok(), await nda.text()).toBeTruthy();

  await page.goto(`/documents/${id}`);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("alert")).toContainText(/NDA/);

  // still there — a reload proves it wasn't deleted, only refused
  await page.reload();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
});

test("deleting an unreferenced document succeeds and removes it from the list", async ({ page }) => {
  const a = await auth(page);
  const title = `Deletable doc ${uniq()}`;
  const { id } = await ownDocument(page, a, title);

  await page.goto(`/documents/${id}`);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/documents$/);
  await expect(page.getByText(title)).toHaveCount(0);
});

test("Details tab: classification, owner, type and next review are all editable and persist", async ({ page }) => {
  const a = await auth(page);
  const title = `Editable details doc ${uniq()}`;
  const { id, people } = await ownDocument(page, a, title);
  expect(people.length, "need a second person to prove the owner actually changed").toBeGreaterThan(1);
  const otherOwner = people.find((p: any) => p.id !== people[0].id) ?? people[0];

  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "Compliance" }).click();
  await page.getByRole("button", { name: "Edit details" }).click();

  await page.getByLabel("Type").selectOption({ label: "Procedure" });
  await page.getByLabel("Classification").selectOption({ label: "Confidential" });
  await page.getByLabel("Owner").selectOption({ value: otherOwner.id });
  await page.getByLabel("Next review").fill("2027-01-15");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("button", { name: "Edit details" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Compliance" }).click();
  // scoped to the rail: the header line right above also shows type/classification/review,
  // so an unscoped match would be ambiguous
  const rail = page.getByRole("complementary", { name: "Compliance" });
  await expect(rail.getByText("procedure", { exact: true })).toBeVisible();
  await expect(rail.getByText("confidential", { exact: true })).toBeVisible();
  await expect(rail.getByText(otherOwner.full_name)).toBeVisible();
  await expect(rail.getByText("2027-01-15")).toBeVisible();
});

test("Linked Controls section attaches and detaches, reflected on the control's own page too", async ({ page }) => {
  const a = await auth(page);
  const { id } = await ownDocument(page, a, `Control-linked doc ${uniq()}`);
  const controls = await (await page.request.get("/api/library/controls", { headers: a })).json();
  const ctl = controls[0];

  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "Compliance" }).click();
  // scoped to the rail: the rich-text editor toolbar has its own unrelated "Link" button
  // (for inserting a hyperlink into the document body), which an unscoped locator also matches
  const rail = page.getByRole("complementary", { name: "Compliance" });
  // Linked Controls renders above Linked Audit Points in the rail
  await rail.getByRole("button", { name: "Link" }).first().click();
  await page.getByPlaceholder("Search controls…").fill(ctl.code);
  await rail.getByRole("button", { name: new RegExp(`^${ctl.code}`) }).click();
  await expect(rail.getByText(`Linked controls · 1`)).toBeVisible();
  await expect(rail.locator("a", { hasText: ctl.code })).toBeVisible();

  // bidirectional: the control's own page shows the document too
  await page.goto(`/controls/view/${ctl.id}`);
  const docTitleLoc = page.locator("a", { hasText: "Control-linked doc" });
  await expect(docTitleLoc).toBeVisible();

  // detach from the document's side
  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "Compliance" }).click();
  await rail.getByRole("button", { name: "remove" }).click();
  await expect(rail.getByText(`Linked controls · 0`)).toBeVisible();
  await page.goto(`/controls/view/${ctl.id}`);
  await expect(page.locator("a", { hasText: "Control-linked doc" })).toHaveCount(0);
});

test("Linked Audit Point section attaches and detaches, reflected in the Workspace drawer too", async ({ page }) => {
  const a = await auth(page);
  const { id } = await ownDocument(page, a, `Audit-linked doc ${uniq()}`);

  const templates = await (await page.request.get("/api/templates", { headers: a })).json();
  const assessTitle = `Doc link spec ${uniq()}`;
  const assess = await page.request.post("/api/assessments", {
    headers: a, data: { template_id: templates[0].id, title: assessTitle } });
  const aid = (await assess.json()).id;
  const detail = await (await page.request.get(`/api/assessments/${aid}`, { headers: a })).json();
  const grid = await (await page.request.get(`/api/assessments/${aid}/questions`, { headers: a })).json();
  const q = grid[0];
  const put = await page.request.put(`/api/assessments/${aid}/responses/${q.question_id}`,
    { headers: a, data: { response_value: "yes" } });
  expect(put.ok(), await put.text()).toBeTruthy();

  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "Compliance" }).click();
  // scoped to the rail: the rich-text editor toolbar has its own unrelated "Link" button
  const rail = page.getByRole("complementary", { name: "Compliance" });
  // Linked Audit Points renders below Linked Controls in the rail
  await rail.getByRole("button", { name: "Link" }).last().click();
  await rail.locator("select").selectOption({ label: `${detail.bank_name} — ${assessTitle}` });
  await rail.getByText(q.text).first().click();
  await expect(rail.getByText(`Linked audit points · 1`)).toBeVisible();

  // the Workspace drawer for that exact question shows the same document
  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator("a", { hasText: "Audit-linked doc" })).toBeVisible();

  // detach from the document's side, confirm it's gone from the drawer too
  await page.goto(`/documents/${id}`);
  await page.getByRole("button", { name: "Compliance" }).click();
  await rail.getByRole("button", { name: "remove" }).click();
  await expect(rail.getByText(`Linked audit points · 0`)).toBeVisible();
  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  await expect(page.getByRole("dialog").locator("a", { hasText: "Audit-linked doc" })).toHaveCount(0);
});
