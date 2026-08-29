import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 5 — Tasks: a completion upload no longer leaks into the general Evidence
 * vault list, completing a task now shows an unmissable confirmation (the list/drawer pill
 * legitimately flips back to "Pending" for a recurring task the moment its next occurrence
 * opens — that's correct, not a bug, so it's left alone), and the edit form now shows a
 * control-generated task's real cadence instead of a misleading blank "One-off" selector.
 *
 * Follow-up (post-Phase-5 user report): a recurring task's own status never becomes
 * "completed", so its finished occurrences were invisible everywhere except a Run History
 * card that — separately — never rendered the attached evidence at all. Fixed together:
 * the Completed tab now also lists a recurring task by its most recent done occurrence, and
 * Run History finally shows what was uploaded.
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

/** Titles are unique per call — several tests in this file create their own task, and a
 * recurring one legitimately stays in the "active" list after being completed, so a loose
 * regex match risks grabbing a DIFFERENT test's row. Always match on the exact title. */
async function ownTask(page: Page, a: Record<string, string>, extra: Record<string, unknown> = {}) {
  const people = await (await page.request.get("/api/people", { headers: a })).json();
  const today = new Date().toISOString().slice(0, 10);
  const title = `E2E task ${uniq()}`;
  const r = await page.request.post("/api/tasks", {
    headers: a, data: { title, assignee_person_id: people[0].id,
      next_due_at: today, ...extra } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { id: (await r.json()).id as string, title };
}

test("a task-completion upload does not appear in the general Evidence vault, but still shows on the task itself", async ({ page }) => {
  const a = await auth(page);
  const { id: taskId, title } = await ownTask(page, a);

  await page.goto("/tasks");
  await page.getByText(title, { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Complete" }).click();

  const fileTitle = `task-proof-${uniq()}.txt`;
  await page.locator('input[type="file"]').setInputFiles({
    name: fileTitle, mimeType: "text/plain", buffer: Buffer.from("proof") });
  await page.getByRole("button", { name: /Mark complete/ }).click();
  await expect(page.getByText("Task completed")).toBeVisible();

  // not in the general vault list
  await page.goto("/evidence");
  await expect(page.getByText(fileTitle.replace(/\.txt$/, ""))).toHaveCount(0);

  // still reachable from the task's own run history
  const detail = await (await page.request.get(`/api/tasks/${taskId}`, { headers: a })).json();
  expect(detail.runs[0].evidence_id).toBeTruthy();
});

test("completing a task shows an immediate confirmation, independent of the next occurrence's pill", async ({ page }) => {
  const a = await auth(page);
  // recurring, so completing it opens a new PENDING occurrence right away — the pill
  // legitimately flips back to Pending, which is exactly what the confirmation exists to
  // disambiguate from "nothing happened"
  const { id: taskId, title } = await ownTask(page, a, { frequency: "MONTHLY", interval_count: 1 });

  await page.goto("/tasks");
  await page.getByText(title, { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Complete" }).click();
  await page.getByRole("button", { name: /Mark complete/ }).click();

  await expect(page.getByRole("status").getByText("Task completed")).toBeVisible();

  // the run that was just completed is "done" — a real, verifiable outcome, not a guess
  const detail = await (await page.request.get(`/api/tasks/${taskId}`, { headers: a })).json();
  const completedRun = detail.runs.find((r: any) => r.status === "done");
  expect(completedRun).toBeTruthy();
});

test("a completed occurrence of a recurring task shows up under the Completed tab, and its evidence appears in Run History", async ({ page }) => {
  const a = await auth(page);
  const { title } = await ownTask(page, a, { frequency: "MONTHLY", interval_count: 1 });

  await page.goto("/tasks");
  await page.getByText(title, { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Complete" }).click();
  const fileTitle = `run-proof-${uniq()}.txt`;
  await page.locator('input[type="file"]').setInputFiles(
    { name: fileTitle, mimeType: "text/plain", buffer: Buffer.from("proof") });
  await page.getByRole("button", { name: /Mark complete/ }).click();
  await expect(page.getByText("Task completed")).toBeVisible();
  await expect(page.getByRole("dialog").filter({ hasText: "Complete task" })).toHaveCount(0);
  // the task drawer stays open by design — its own backdrop still covers the list behind
  // it, so it has to be closed before the Completed tab underneath is reachable
  await dialog.getByRole("button", { name: "Close" }).click();

  // still "active" overall (it's recurring, and a new occurrence just opened) — but the
  // Completed tab lists it too, by its most recent finished occurrence
  await page.getByRole("button", { name: /^completed$/i }).click();
  const row = page.locator("tr", { hasText: title });
  await expect(row).toBeVisible();
  await expect(row.getByText(/^Completed \d{4}-\d{2}-\d{2}/)).toBeVisible();

  // the uploaded file is visible in Run History, not just silently attached
  const evidenceTitle = fileTitle.replace(/\.txt$/, "");
  await row.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("button", { name: evidenceTitle })).toBeVisible();
});

test("editing a control-generated recurring task shows its real cadence, not a blank One-off selector", async ({ page }) => {
  const a = await auth(page);
  const { title } = await ownTask(page, a, { cadence_months: 3 });

  await page.goto("/tasks");
  await page.getByText(title, { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Every 3 mo")).toBeVisible();   // the drawer already got this right

  await dialog.getByRole("button", { name: "Edit" }).click();
  const editDialog = page.getByRole("dialog").last();
  await expect(editDialog.getByText(/Every 3 mo/)).toBeVisible();
  await expect(editDialog.getByText(/not editable here/)).toBeVisible();
  // and the misleading editable "One-off" selector is gone for this task
  await expect(editDialog.locator("select").filter({ hasText: "One-off" })).toHaveCount(0);
});
