import { test, expect, Page } from "@playwright/test";

/**
 * P4-S9 — the tasks / compliance calendar module, in a real browser.
 *
 * tests/test_tasks_s9.py proves the API: recurrence arithmetic, PATCH validation, the
 * dormant risk_id/document_id/assignee_person_id links, pause suppressing overdue flips,
 * and — the headline fix — complete_run anchoring on the run's own due date instead of
 * today. What only a browser proves: that a hand-created task can actually be built with
 * the new recurrence UI at all (there was no create form before this sprint), that pausing
 * really hides a task from the default list, and that `/tasks/calendar` — built and never
 * consumed until now — renders something real.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function apiGet(page: Page, path: string): Promise<any> {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

/* `getByLabel("Every", { exact: true })` — the exactness is load-bearing. Playwright matches
   labels as a case-insensitive SUBSTRING by default, so a bare "Every" also matches the global
   search box added in P5-S6, whose accessible name is "Search everything". Same trap as the
   `getByText("Photo")` collision in 46-registers-s7. */
test.describe("create", () => {
  test("a recurring task can be built with the new frequency/interval fields", async ({ page }) => {
    const title = `E2E recurring ${uniq()}`;
    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByLabel("Recurrence").selectOption("WEEKLY");
    await page.getByLabel("Every", { exact: true }).fill("2");
    await page.getByLabel(/due$/i).fill("2027-01-04");
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const rows = await apiGet(page, "/tasks?status=all");
    const made = rows.find((t: any) => t.title === title);
    expect(made, "the task should exist").toBeTruthy();
    expect(made.frequency).toBe("WEEKLY");
    expect(made.interval_count).toBe(2);
    expect(made.cadence_months).toBeNull();

    await expect(page.locator("tbody tr", { hasText: title })).toContainText("Every 2 weeks");
  });

  test("a one-off task needs no recurrence fields at all", async ({ page }) => {
    const title = `E2E one-off ${uniq()}`;
    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    // Recurrence stays "One-off" — no interval box should even render
    await expect(page.getByLabel("Every", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.locator("tbody tr", { hasText: title })).toContainText("One-off");
  });
});

test.describe("pause / resume", () => {
  test("pausing removes a task from the default list and it reappears under Paused", async ({ page }) => {
    const title = `E2E pause ${uniq()}`;
    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: title }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Pause" }).click();
    await expect(drawer.getByText("Paused")).toBeVisible();
    await drawer.getByRole("button", { name: "Close" }).click();

    await expect(page.locator("tbody tr", { hasText: title })).toHaveCount(0);
    await page.getByRole("button", { name: "paused", exact: true }).click();
    await expect(page.locator("tbody tr", { hasText: title })).toBeVisible();

    await page.locator("tbody tr", { hasText: title }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Resume" }).click();
    await expect(page.getByRole("dialog").getByText("Active")).toBeVisible();
  });
});

test.describe("edit", () => {
  test("editing a task changes its recurrence without losing the assignee link", async ({ page }) => {
    await page.goto("/tasks");
    const people = await apiGet(page, "/people");
    test.skip(people.length === 0, "needs a seeded person");
    const title = `E2E edit ${uniq()}`;

    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByLabel("Assignee").selectOption(people[0].id);
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: title }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Edit" }).click();
    const editDialog = page.getByRole("dialog").last();
    await editDialog.getByLabel("Recurrence").selectOption("MONTHLY");
    await editDialog.getByLabel("Every", { exact: true }).fill("1");
    await editDialog.getByRole("button", { name: "Save" }).click();

    const rows = await apiGet(page, "/tasks?status=all");
    const made = rows.find((t: any) => t.title === title);
    expect(made.frequency).toBe("MONTHLY");
    expect(made.assignee_person_id).toBe(people[0].id);
  });
});

test.describe("complete rolls the schedule forward correctly", () => {
  test("completing a run early does not drag the whole schedule later", async ({ page }) => {
    // The headline regression: complete_run() used to anchor on TODAY, so finishing a
    // monthly task two weeks early permanently shifted every future occurrence two weeks
    // late. It now anchors on the run's own due date.
    const title = `E2E anchor ${uniq()}`;
    const dueDate = new Date(); dueDate.setDate(dueDate.getDate() + 14);
    const dueIso = dueDate.toISOString().slice(0, 10);

    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByLabel("Recurrence").selectOption("MONTHLY");
    await page.getByLabel("Every", { exact: true }).fill("1");
    await page.getByLabel(/due$/i).fill(dueIso);
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const before = (await apiGet(page, "/tasks?status=all")).find((t: any) => t.title === title);

    await page.locator("tbody tr", { hasText: title }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Complete" }).click();
    await page.getByRole("dialog").getByRole("button", { name: /Mark complete/ }).click();
    await expect(page.getByText(/Completing…/)).toHaveCount(0);

    const after = (await apiGet(page, "/tasks?status=all")).find((t: any) => t.title === title);
    // due_at + 1 month, not today + 1 month — those differ by the ~14 days completed early
    const expected = new Date(before.next_due_at ?? dueIso);
    expected.setMonth(expected.getMonth() + 1);
    expect(after.next_due_at?.slice(0, 10)).toBe(expected.toISOString().slice(0, 10));
  });
});

test.describe("calendar", () => {
  test("a task's next run shows up on the calendar tab for its due month", async ({ page }) => {
    const title = `E2E calendar ${uniq()}`;
    // The 1st of the CURRENT month, not "+N days" — a few days out can cross a month
    // boundary (e.g. run on the 30th/31st) and land outside the calendar's default view.
    const due = new Date(); due.setDate(1);
    const dueIso = due.toISOString().slice(0, 10);

    await page.goto("/tasks");
    await page.getByRole("button", { name: /New task/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByLabel(/due$/i).fill(dueIso);
    await page.getByRole("button", { name: "Create task" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.getByRole("button", { name: "calendar", exact: true }).click();
    await expect(page.getByText(title)).toBeVisible();
  });
});

test.describe("permissions", () => {
  test("a Viewer sees no write affordances on Tasks", async ({ page, browser }) => {
    const n = uniq();
    await page.goto("/tasks");
    const token = await page.evaluate(() => localStorage.getItem("ar_token"));
    const made = await page.request.post("/api/e2e/make-member", {
      headers: { Authorization: `Bearer ${token}` },
      data: { email: `tasks-viewer-${n}@example.com`, full_name: "Read Only",
              role_name: "Viewer", password: "Passw0rdOne" },
    });
    expect(made.ok(), await made.text()).toBeTruthy();

    const viewerCtx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const viewer = await viewerCtx.newPage();
    await viewer.goto("/login");
    await viewer.getByLabel("Email").fill(`tasks-viewer-${n}@example.com`);
    await viewer.getByLabel("Password").fill("Passw0rdOne");
    await viewer.getByRole("button", { name: "Sign in" }).click();

    await viewer.goto("/tasks");
    await expect(viewer.getByRole("button", { name: /New task/ })).toHaveCount(0);
    await viewerCtx.close();
  });
});
