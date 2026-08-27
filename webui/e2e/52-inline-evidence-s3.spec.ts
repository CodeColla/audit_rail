import { test, expect, Page } from "@playwright/test";

/**
 * P5-S3 — attaching evidence WITHOUT a trip to the vault.
 *
 * Why this sprint exists, from the activity log of 2026-07-31: with the vault emptied, a
 * task was completed by linking a three-day-old, unrelated PDF — because picking something
 * already uploaded was the only option the UI offered.
 *
 * The implementation is deliberately two existing API calls (`POST /evidence`, then the
 * endpoint's existing `evidence_id`) rather than multipart variants of `complete` and the
 * response-evidence route: FastAPI cannot serve JSON and multipart on one path, so adding
 * the file would have meant either breaking the existing contract or duplicating upload
 * logic across two more routes. See docs/phase5/03-sprint-plan.md S3.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);
const dueToday = () => new Date().toISOString().slice(0, 10);
const txt = (name: string) =>
  ({ name, mimeType: "text/plain", buffer: Buffer.from("proof of work") });

async function apiGet(page: Page, path: string) {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok(), `${path} -> ${r.status()}`).toBeTruthy();
  return r.json();
}

test.describe("completing a task", () => {
  test("a file uploaded here attaches to the run, without leaking into the general vault", async ({ page }) => {
    // issue #13: this upload used to land in BOTH the run and the general Evidence vault
    // list, mixing task-completion artifacts in with deliberately-curated evidence. The
    // fix keeps it attached to the run (still fetchable by id — the task drawer's own
    // AttachmentLink relies on exactly that) but excludes it from GET /evidence by default.
    const tag = `s3task-${uniq()}`;
    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(tag);
    // A run only exists once the task has a due date, and the modal only opens from a run.
    await page.getByLabel(/due$/i).fill(dueToday());
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: tag }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Complete" }).click();
    const modal = page.getByRole("dialog").filter({ hasText: "Complete task" });
    await modal.locator('input[type="file"]').setInputFiles(txt(`${tag}.txt`));
    await expect(modal.getByText(`Selected: ${tag}.txt`)).toBeVisible();

    // picking a file must disable the vault dropdown — attaching both would be ambiguous
    await expect(modal.locator("select")).toBeDisabled();

    await modal.getByRole("button", { name: /Mark complete/ }).click();
    await expect(page.getByText("Task completed")).toBeVisible();
    // Assert the MODAL closed, not "no dialogs" — the task drawer underneath is also
    // role="dialog" and legitimately stays open.
    await expect(modal).toHaveCount(0);

    const tasks = await apiGet(page, "/tasks?status=all");
    const task = tasks.find((t: any) => t.title === tag);
    const detail = await apiGet(page, `/tasks/${task.id}`);
    const evidenceId = detail.runs[0].evidence_id;
    expect(evidenceId, "the completed run should carry the uploaded evidence's id").toBeTruthy();

    // NOT in the general vault list…
    const vault = await apiGet(page, "/evidence");
    expect(vault.some((e: any) => e.id === evidenceId),
      "a task-completion upload must not appear in the general vault list").toBeFalsy();

    // …but the artifact itself still exists, reachable by id
    const artifact = await apiGet(page, `/evidence/${evidenceId}`);
    expect(artifact.title).toBe(tag);
  });

  test("completing with no file at all still works", async ({ page }) => {
    // S3 added an option, not a requirement — plenty of tasks produce no artifact.
    const tag = `s3none-${uniq()}`;
    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(tag);
    await page.getByLabel(/due$/i).fill(dueToday());
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: tag }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Complete" }).click();
    await page.getByRole("dialog").filter({ hasText: "Complete task" })
      .getByRole("button", { name: /Mark complete/ }).click();
    await expect(page.getByText(/Completing…/)).toHaveCount(0);
  });
});

test.describe("attaching evidence to an audit question", () => {
  test("a file uploaded on the question attaches to it and reaches the vault", async ({ page, browser }) => {
    // Build the assessment through the API — the UI journey for that is already covered by
    // 30-audit-journey; what is new here is uploading proof from the question itself.
    const tag = uniq();
    const token = await (async () => {
      await page.goto("/");
      return page.evaluate(() => localStorage.getItem("ar_token"));
    })();
    const h = { Authorization: `Bearer ${token}` };
    const api = (await browser.newContext()).request;

    const templates = await (await api.get("/api/templates", { headers: h })).json();
    const tpl = (templates.items ?? templates)[0];
    const aid = (await (await api.post("/api/assessments", {
      headers: h, data: { template_id: tpl.id, title: `S3 audit ${tag}` } })).json()).id;
    const grid = await (await api.get(`/api/assessments/${aid}/questions`, { headers: h })).json();
    const qid = grid[0].question_id;
    // evidence can only be linked once the question is answered
    await api.put(`/api/assessments/${aid}/responses/${qid}`, {
      headers: h, data: { response_value: "yes", comment: "answered" } });

    await page.goto(`/audits/${aid}`);
    await page.locator("tbody tr").first().click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("Linked evidence")).toBeVisible();

    await drawer.locator('input[type="file"]').setInputFiles(txt(`${tag}-audit.txt`));

    // it appears on the question without a trip to the vault…
    await expect(drawer.getByRole("button", { name: `${tag}-audit` })).toBeVisible({ timeout: 15_000 });

    // …and it really is in the vault, linked to this response
    const detail = await apiGet(page, `/assessments/${aid}/responses/${qid}`);
    expect(detail.evidence.some((e: any) => e.title === `${tag}-audit`)).toBeTruthy();
  });
});
