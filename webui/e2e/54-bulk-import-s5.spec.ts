import { test, expect, Page } from "@playwright/test";

/**
 * P5-S5 — bulk import across the registers, and real deletion of people.
 *
 * The import is deliberately explicit: the column list comes from the server (the same spec
 * that builds the template and the row builder), and mapping is pre-filled only on an EXACT
 * header match. Fuzzy-matching a column is how a bulk import quietly fills the wrong field
 * and still reports success.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);
const csv = (body: string) =>
  ({ name: "import.csv", mimeType: "text/csv", buffer: Buffer.from(body) });

async function apiPost(page: Page, path: string, data: unknown) {
  // localStorage only exists once the page is on the origin — reading it from about:blank
  // throws SecurityError.
  if (!page.url().startsWith("http")) await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  return page.request.post(`/api${path}`,
    { headers: { Authorization: `Bearer ${token}` }, data: data as any });
}

test("import risks from a CSV, with a bad row reported and the rest saved", async ({ page }) => {
  const tag = uniq();
  await page.goto("/risks");
  await page.getByRole("button", { name: /Import/ }).click();
  const modal = page.getByRole("dialog");

  await modal.locator('input[type="file"]').setInputFiles(csv(
    `Title,Reference,Inherent impact\n` +
    `${tag} alpha,R-${tag}-1,3\n` +
    `,R-${tag}-2,3\n` +               // no title — required
    `${tag} gamma,R-${tag}-3,99\n` +  // score out of range
    `${tag} delta,R-${tag}-4,2\n`));

  // headers matched our labels exactly, so the mapping pre-fills and Import is enabled
  await expect(modal.getByRole("button", { name: "Import", exact: true })).toBeEnabled();
  await modal.getByRole("button", { name: "Import", exact: true }).click();

  await expect(modal.getByText(/Imported/)).toBeVisible();
  await expect(modal.getByText(/2 rows could not be imported/)).toBeVisible();
  // the failures name the ROW and the reason — a count alone is unactionable
  await expect(modal.getByText("row 3")).toBeVisible();
  await expect(modal.getByText(/Title is required/)).toBeVisible();
  await expect(modal.getByText(/1 to 5/)).toBeVisible();

  await modal.getByRole("button", { name: "Done" }).click();
  await page.getByPlaceholder("Search risks…").fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(2);
});

test("an ambiguous owner fails its row instead of guessing", async ({ page }) => {
  const tag = uniq();
  const dupe = `Twin ${tag}`;
  await apiPost(page, "/people", { full_name: dupe, email: `t1-${tag}@kiam.example` });
  await apiPost(page, "/people", { full_name: dupe, email: `t2-${tag}@kiam.example` });

  await page.goto("/risks");
  await page.getByRole("button", { name: /Import/ }).click();
  const modal = page.getByRole("dialog");
  await modal.locator('input[type="file"]').setInputFiles(csv(
    `Title,Owner\n${tag} owned,${dupe}\n`));
  await modal.getByRole("button", { name: "Import", exact: true }).click();

  // scoped to the error row: the modal's own help text also mentions email addresses
  const rowError = modal.locator(".text-bad").filter({ hasText: /more than one/ });
  await expect(rowError).toBeVisible();
  await expect(rowError).toContainText("email");
});

test("a file whose headers we do not recognise needs mapping before Import unlocks", async ({ page }) => {
  const tag = uniq();
  await page.goto("/assets");
  await page.getByRole("button", { name: /Import/ }).click();
  const modal = page.getByRole("dialog");
  await modal.locator('input[type="file"]').setInputFiles(csv(`Machine,Where\n${tag} box,Rack 4\n`));

  // nothing auto-mapped, so the required column is unsatisfied and Import stays disabled
  await expect(modal.getByText(/Still need a column for Name/)).toBeVisible();
  await expect(modal.getByRole("button", { name: "Import", exact: true })).toBeDisabled();

  await modal.locator("select").first().selectOption("Machine");
  await expect(modal.getByRole("button", { name: "Import", exact: true })).toBeEnabled();
  await modal.getByRole("button", { name: "Import", exact: true }).click();
  await expect(modal.getByText(/Imported/)).toBeVisible();
});

test("every register offers a template download", async ({ page }) => {
  for (const path of ["/risks", "/assets", "/data", "/third-parties", "/incidents"]) {
    await page.goto(path);
    await page.getByRole("button", { name: /Import/ }).click();
    const [dl] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("dialog").getByRole("button", { name: /Download the template/ }).click(),
    ]);
    expect(await dl.failure(), path).toBeNull();
    expect(dl.suggestedFilename()).toMatch(/\.xlsx$/);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  }
});

test("a person who owns something cannot be deleted, and the reason says why", async ({ page }) => {
  const tag = uniq();
  const person = await (await apiPost(page, "/people",
    { full_name: `Owner ${tag}`, email: `own-${tag}@kiam.example` })).json();
  await apiPost(page, "/risks", { title: `${tag} owned risk`, owner_person_id: person.id });

  await page.goto("/people");
  const row = page.locator("tbody tr", { hasText: `Owner ${tag}` });
  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: `Delete Owner ${tag}` }).click();

  await expect(page.getByText(/still referenced by/)).toBeVisible();
  await expect(page.getByText(/risk/)).toBeVisible();
  await expect(row).toHaveCount(1);           // refused, not partially applied
});

test("an unreferenced person really is deleted", async ({ page }) => {
  const tag = uniq();
  await apiPost(page, "/people", { full_name: `Spare ${tag}`, email: `sp-${tag}@kiam.example` });
  await page.goto("/people");
  const row = page.locator("tbody tr", { hasText: `Spare ${tag}` });
  await expect(row).toHaveCount(1);

  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: `Delete Spare ${tag}` }).click();
  await expect(page.locator("tbody tr", { hasText: `Spare ${tag}` })).toHaveCount(0);
});
