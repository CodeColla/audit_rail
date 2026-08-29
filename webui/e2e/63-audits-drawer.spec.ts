import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 1 — the Workspace question drawer.
 *
 * Three confirmed gaps, scoped precisely after the owner pointed at exactly where each one
 * lives (the grid row's own inline number edit and the Control page's own remove buttons
 * already worked — these are the parts that didn't):
 *   1. the drawer's header showed the point number read-only, with no edit affordance at all.
 *   2. a DIRECTLY-linked evidence/document/incident/asset had no detach action.
 *   3. an item inherited from the question's mapped control ("via control") had no detach
 *      action either — and since there is no per-question override table, detaching one there
 *      must remove it from the CONTROL itself, which is a wider blast radius than a normal
 *      unlink and needs its own confirmation.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

/** Same pattern as 45-audits-remap.spec.ts: the seed ships zero assessments, so every spec
 * that needs one makes its own from a seeded template. */
async function ownAssessment(page: Page) {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  expect(templates.length, "the e2e seed should ship templates").toBeGreaterThan(0);
  const r = await page.request.post("/api/assessments", {
    headers: auth,
    data: { template_id: templates[0].id, title: `Drawer spec ${uniq()}` },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { aid: (await r.json()).id, auth };
}

async function grid(page: Page, aid: string, auth: Record<string, string>) {
  const rows = await (await page.request.get(`/api/assessments/${aid}/questions`, { headers: auth })).json();
  expect(rows.length, "the seeded template should have questions").toBeGreaterThan(1);
  return rows;
}

/** Answers the first question via the API — a response row must exist before evidence,
 * documents, incidents or assets can be linked to it directly (assessments.py's link_*
 * routes all 404 "answer the question first" otherwise). */
async function answeredQuestion(page: Page) {
  const { aid, auth } = await ownAssessment(page);
  const rows = await grid(page, aid, auth);
  const q = rows[0];
  const put = await page.request.put(`/api/assessments/${aid}/responses/${q.question_id}`,
    { headers: auth, data: { response_value: "yes" } });
  expect(put.ok(), await put.text()).toBeTruthy();
  return { aid, auth, qid: q.question_id, text: q.text as string };
}

async function uploadEvidence(page: Page, auth: Record<string, string>, title: string) {
  const up = await page.request.post("/api/evidence", {
    headers: auth,
    multipart: { title, evidence_type: "REPORT",
      file: { name: `${title}.txt`, mimeType: "text/plain", buffer: Buffer.from("x") } },
  });
  expect(up.ok(), await up.text()).toBeTruthy();
  return (await up.json()).id as string;
}

test("editing the point number inside the drawer updates it everywhere", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);
  const rows = await grid(page, aid, auth);
  const q = rows[0];

  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  const dialog = page.getByRole("dialog");

  const newNumber = `X-${uniq()}`;
  await dialog.getByRole("button", { name: /^Edit number for question/ }).click();
  const input = dialog.getByLabel("Question number");
  await input.fill(newNumber);
  await input.press("Enter");

  // the drawer's own header reflects the new number once the save round-trips
  await expect(dialog.getByRole("button", { name: `Edit number for question ${newNumber}` })).toBeVisible();

  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.locator("table tbody tr").first()).toContainText(newNumber);
});

test("removing a directly-linked evidence item detaches it without deleting it from the vault", async ({ page }) => {
  const { aid, auth, qid, text } = await answeredQuestion(page);
  const title = `Direct evidence ${uniq()}`;
  const evId = await uploadEvidence(page, auth, title);
  const link = await page.request.post(`/api/assessments/${aid}/responses/${qid}/evidence`,
    { headers: auth, data: { evidence_id: evId } });
  expect(link.ok(), await link.text()).toBeTruthy();

  await page.goto(`/audits/${aid}`);
  await page.getByText(text).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: title })).toBeVisible();

  await dialog.getByRole("button", { name: "remove" }).click();
  await expect(dialog.getByRole("button", { name: title })).toHaveCount(0);

  // still in the vault — only the link was removed, not the record
  await page.goto("/evidence");
  await expect(page.getByText(title)).toBeVisible();
});

test("removing a directly-linked document detaches it without deleting it", async ({ page }) => {
  const { aid, auth, qid, text } = await answeredQuestion(page);
  const people = await (await page.request.get("/api/people", { headers: auth })).json();
  expect(people.length, "the e2e seed should ship people").toBeGreaterThan(0);
  const title = `Direct document ${uniq()}`;
  const doc = await page.request.post("/api/documents", {
    headers: auth, data: { title, owner_person_id: people[0].id } });
  expect(doc.ok(), await doc.text()).toBeTruthy();
  const docId = (await doc.json()).id;
  const link = await page.request.post(`/api/assessments/${aid}/responses/${qid}/documents`,
    { headers: auth, data: { document_id: docId } });
  expect(link.ok(), await link.text()).toBeTruthy();

  await page.goto(`/audits/${aid}`);
  await page.getByText(text).first().click();
  const dialog = page.getByRole("dialog");
  const row = dialog.locator("a", { hasText: title }).locator("..");
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "remove" }).click();
  await expect(dialog.locator("a", { hasText: title })).toHaveCount(0);

  await page.goto("/documents");
  await expect(page.getByText(title)).toBeVisible();
});

test("detaching an inherited evidence item removes it from the control, affecting every audit point mapped to it", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);
  const rows = await grid(page, aid, auth);
  const [qA, qB] = rows;

  const controls = await (await page.request.get("/api/library/controls", { headers: auth })).json();
  expect(controls.length).toBeGreaterThan(0);
  const ctl = controls[0];

  // force BOTH questions onto the same control, deterministically, rather than hoping the
  // seeded checklist happens to map two points to one control on its own
  for (const q of [qA, qB]) {
    const r = await page.request.patch(`/api/assessments/${aid}/responses/${q.question_id}/mapping`,
      { headers: auth, data: { control_id: ctl.id } });
    expect(r.ok(), await r.text()).toBeTruthy();
  }

  const title = `Inherited evidence ${uniq()}`;
  const evId = await uploadEvidence(page, auth, title);
  const link = await page.request.post(`/api/library/controls/${ctl.id}/evidence`,
    { headers: auth, data: { evidence_id: evId } });
  expect(link.ok(), await link.text()).toBeTruthy();

  await page.goto(`/audits/${aid}`);
  await page.getByText(qA.text).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Inherited from ${ctl.code}`)).toBeVisible();
  await expect(dialog.getByRole("button", { name: title })).toBeVisible();

  // scoped to THIS item's own row: the shared control this test picks (controls[0]) can
  // carry other inherited evidence/documents left over from unrelated activity in the same
  // e2e database, each with their own identically-labelled "detach from control" button
  const row = dialog.getByRole("button", { name: title }).locator("../..");
  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: "detach from control" }).click();
  await expect(dialog.getByRole("button", { name: title })).toHaveCount(0);

  // the SECOND question mapped to the same control also lost it — proves the detach hit
  // the control, not just this one response
  await dialog.getByRole("button", { name: "Close" }).click();
  await page.getByText(qB.text).first().click();
  await expect(page.getByRole("dialog").getByRole("button", { name: title })).toHaveCount(0);

  // the evidence itself is untouched — still in the vault, just unlinked from the control
  await page.goto("/evidence");
  await expect(page.getByText(title)).toBeVisible();
});

test("an inherited document also shows a detach-from-control action", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);
  const rows = await grid(page, aid, auth);
  const q = rows[0];

  const controls = await (await page.request.get("/api/library/controls", { headers: auth })).json();
  const ctl = controls[0];
  await page.request.patch(`/api/assessments/${aid}/responses/${q.question_id}/mapping`,
    { headers: auth, data: { control_id: ctl.id } });

  const people = await (await page.request.get("/api/people", { headers: auth })).json();
  const title = `Inherited document ${uniq()}`;
  const doc = await page.request.post("/api/documents", {
    headers: auth, data: { title, owner_person_id: people[0].id } });
  const docId = (await doc.json()).id;
  const link = await page.request.post(`/api/library/controls/${ctl.id}/documents`,
    { headers: auth, data: { document_id: docId } });
  expect(link.ok(), await link.text()).toBeTruthy();

  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Inherited from ${ctl.code}`)).toBeVisible();
  const row = dialog.locator("a", { hasText: title }).locator("..");
  await expect(row).toBeVisible();

  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: "detach from control" }).click();
  await expect(dialog.locator("a", { hasText: title })).toHaveCount(0);

  await page.goto("/documents");
  await expect(page.getByText(title)).toBeVisible();
});

test("the audit points grid shows a Documents link-count column, alongside the existing Evidence one", async ({ page }) => {
  const { aid, auth, qid, text } = await answeredQuestion(page);
  const people = await (await page.request.get("/api/people", { headers: auth })).json();
  const doc = await page.request.post("/api/documents", {
    headers: auth, data: { title: `Grid-linked document ${uniq()}`, owner_person_id: people[0].id } });
  expect(doc.ok(), await doc.text()).toBeTruthy();
  const docId = (await doc.json()).id;
  const link = await page.request.post(`/api/assessments/${aid}/responses/${qid}/documents`,
    { headers: auth, data: { document_id: docId } });
  expect(link.ok(), await link.text()).toBeTruthy();

  await page.goto(`/audits/${aid}`);
  await expect(page.getByRole("columnheader", { name: "Documents" })).toBeVisible();
  const row = page.locator("tr", { hasText: text });
  await expect(row).toContainText("1 linked");
});
