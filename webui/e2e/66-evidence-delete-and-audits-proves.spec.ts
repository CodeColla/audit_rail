import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 4 — Evidence: delete always succeeds now (clearing references instead of
 * being blocked by them, the opposite policy from Documents' guarded delete in Phase 3), plus
 * the new "Audits This Proves" section mirroring the existing "Controls This Proves" one.
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

async function uploadEvidence(page: Page, a: Record<string, string>, title: string) {
  const up = await page.request.post("/api/evidence", {
    headers: a, multipart: { title, evidence_type: "REPORT",
      file: { name: `${title}.txt`, mimeType: "text/plain", buffer: Buffer.from("x") } } });
  expect(up.ok(), await up.text()).toBeTruthy();
  return (await up.json()).id as string;
}

test("deleting evidence referenced by a completed task run succeeds and clears the link", async ({ page }) => {
  const a = await auth(page);
  const title = `Task-referenced evidence ${uniq()}`;
  const evId = await uploadEvidence(page, a, title);

  const people = await (await page.request.get("/api/people", { headers: a })).json();
  const today = new Date().toISOString().slice(0, 10);
  const task = await page.request.post("/api/tasks", {
    headers: a, data: { title: `E2E task ${uniq()}`, assignee_person_id: people[0].id,
      next_due_at: today } });
  expect(task.ok(), await task.text()).toBeTruthy();
  const taskId = (await task.json()).id;
  const detail = await (await page.request.get(`/api/tasks/${taskId}`, { headers: a })).json();
  const runId = detail.runs[0].id;
  const complete = await page.request.post(`/api/tasks/${taskId}/runs/${runId}/complete`,
    { headers: a, data: { evidence_id: evId } });
  expect(complete.ok(), await complete.text()).toBeTruthy();

  await page.goto("/evidence");
  page.once("dialog", (d) => d.accept());
  const row = page.locator("tr", { hasText: title });
  await row.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText(title)).toHaveCount(0);

  // the task run itself is untouched — it just no longer references the deleted evidence
  const runsAfter = await (await page.request.get(`/api/tasks/${taskId}`, { headers: a })).json();
  expect(runsAfter.runs[0].evidence_id).toBeNull();
  expect(runsAfter.runs[0].status).toBe("done");
});

test("Audits This Proves: linking from the evidence side shows up in the audit's own drawer", async ({ page }) => {
  const a = await auth(page);
  const title = `Proves-audit evidence ${uniq()}`;
  const evId = await uploadEvidence(page, a, title);

  const templates = await (await page.request.get("/api/templates", { headers: a })).json();
  const assessTitle = `Evidence proves spec ${uniq()}`;
  const assess = await page.request.post("/api/assessments", {
    headers: a, data: { template_id: templates[0].id, title: assessTitle } });
  const aid = (await assess.json()).id;
  const bankDetail = await (await page.request.get(`/api/assessments/${aid}`, { headers: a })).json();
  const grid = await (await page.request.get(`/api/assessments/${aid}/questions`, { headers: a })).json();
  const q = grid[0];
  const put = await page.request.put(`/api/assessments/${aid}/responses/${q.question_id}`,
    { headers: a, data: { response_value: "yes" } });
  expect(put.ok(), await put.text()).toBeTruthy();

  await page.goto(`/evidence/view/${evId}`);
  await page.getByRole("button", { name: "Link an audit point" }).click();
  await page.locator("select").selectOption({ label: `${bankDetail.bank_name} — ${assessTitle}` });
  await page.getByText(q.text).first().click();
  await expect(page.getByText("Audits this proves · 1")).toBeVisible();

  // shows up in the Workspace drawer for that exact question too
  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: title })).toBeVisible();

  // unlink from the evidence side, confirm it's gone from the drawer too
  await page.goto(`/evidence/view/${evId}`);
  await page.getByRole("button", { name: "remove" }).click();
  await expect(page.getByText("Audits this proves · 0")).toBeVisible();
  await page.goto(`/audits/${aid}`);
  await page.getByText(q.text).first().click();
  await expect(page.getByRole("dialog").getByRole("button", { name: title })).toHaveCount(0);
});

test("Controls This Proves is unaffected by the new Audits section", async ({ page }) => {
  const a = await auth(page);
  const title = `Regression-check evidence ${uniq()}`;
  const evId = await uploadEvidence(page, a, title);
  const controls = await (await page.request.get("/api/library/controls", { headers: a })).json();
  const ctl = controls[0];

  await page.goto(`/evidence/view/${evId}`);
  await page.getByRole("button", { name: "Link a control" }).click();
  await page.getByPlaceholder("Search by reference or statement…").fill(ctl.code);
  await page.getByRole("button", { name: new RegExp(`^${ctl.code}`) }).click();
  await expect(page.getByText(/Controls this proves · 1/)).toBeVisible();
  await expect(page.locator("a", { hasText: ctl.code })).toBeVisible();
});
