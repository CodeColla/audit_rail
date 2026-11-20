import { test, expect, Page } from "@playwright/test";

/**
 * #20 S4 — backfill several files to one past year of a recurring task.
 *
 * What a browser must prove: the modal attaches every selected file to the chosen year, and
 * the task's schedule is exactly where it was afterwards. A backfill is not a completion. The
 * API side is in tests/test_tasks_backfill.py.
 *
 * Setup goes through the API (a yearly task with a fixed next_due_at), so no test-only hook is
 * needed. Not run yet: the e2e login fixture is missing from the database this runs against.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function authHeaders(page: Page) {
  await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  return { Authorization: `Bearer ${token}` };
}

test.describe("task backfill (#20 S4)", () => {
  test("three files attach to a chosen past year and the schedule does not move", async ({ page }) => {
    const title = `Backfill ${uniq()}`;
    const headers = await authHeaders(page);
    const created = await page.request.post("/api/tasks", {
      headers,
      data: { title, frequency: "YEARLY", interval_count: 1, next_due_at: "2026-06-15" },
    });
    expect(created.status(), await created.text()).toBe(201);
    const taskId = (await created.json()).id;

    const before = await (await page.request.get(`/api/tasks/${taskId}`, { headers })).json();

    await page.goto("/tasks");
    await page.getByText(title, { exact: true }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Backfill evidence" }).click();

    const modal = page.getByRole("dialog", { name: "Backfill evidence" });
    await modal.getByLabel("Year").selectOption("2024");
    await modal.getByLabel("Files").setInputFiles(
      ["a", "b", "c"].map((n) => ({
        name: `${n}.pdf`, mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4\n% backfill e2e\n"),
      })),
    );
    await modal.getByRole("button", { name: "Attach files" }).click();
    await expect(modal.getByRole("status")).toHaveText("3 file(s) attached to 2024.");

    const after = await (await page.request.get(`/api/tasks/${taskId}`, { headers })).json();
    expect(after.next_due_at).toEqual(before.next_due_at);
    expect(after.runs.filter((r: { status: string }) => r.status === "pending").length)
      .toEqual(before.runs.filter((r: { status: string }) => r.status === "pending").length);
  });
});
