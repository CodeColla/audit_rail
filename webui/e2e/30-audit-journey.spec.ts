import { test, expect } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CHECKLIST = path.join(HERE, "fixtures/mini-bank-checklist.csv");
const BANK = "E2E Bank";

/**
 * The original product, end to end: import a bank checklist, confirm the proposed control
 * mappings, create the assessment, then work it — prefill, answer, thread, finding, export.
 *
 * Serial by design: each step builds on the last, and the whole point is that the JOURNEY
 * holds together. The seed deliberately ships zero assessments, so step 1 creates the one
 * the rest of the file uses.
 */
test.describe.configure({ mode: "serial" });

let workspaceUrl = "";

test("import a checklist and get proposed control mappings", async ({ page }) => {
  await page.goto("/audits");
  await expect(page.getByText(/No assessments yet/)).toBeVisible();

  await page.getByRole("link", { name: /Import checklist/ }).click();
  await expect(page.getByRole("heading", { name: "Import a bank checklist" })).toBeVisible();

  await page.getByLabel("Bank name").fill(BANK);
  await page.getByLabel(/^Version/).fill("v1.0");
  // the real input is visually hidden behind the drop-zone label
  await page.locator('input[type="file"]').setInputFiles(CHECKLIST);
  await expect(page.getByText("mini-bank-checklist.csv")).toBeVisible();

  await page.getByRole("button", { name: /Import & propose mappings/ }).click();

  // review step: all six questions parsed out of the CSV
  await expect(page.getByRole("heading", { name: `${BANK} — review mappings` })).toBeVisible();
  await expect(page.getByText("6 questions")).toBeVisible();
  await expect(page.getByText(/Do you enforce multi-factor authentication/)).toBeVisible();
});

test("confirm the mappings and create the assessment", async ({ page }) => {
  // (continues from the previous test — the wizard keeps its state in the page)
  await page.goto("/audits/import");
  await page.getByLabel("Bank name").fill(BANK);
  await page.locator('input[type="file"]').setInputFiles(CHECKLIST);
  await page.getByRole("button", { name: /Import & propose mappings/ }).click();
  await expect(page.getByText("6 questions")).toBeVisible();

  await page.getByRole("button", { name: /Confirm all/ }).click();
  await page.getByRole("button", { name: /Create assessment/ }).click();

  await expect(page).toHaveURL(/\/audits\/[0-9a-f-]{36}/);
  workspaceUrl = page.url();
  await expect(page.getByRole("button", { name: "Prefill from library" })).toBeVisible();
});

test("answer a question, with the N/A justification actually enforced", async ({ page }) => {
  await page.goto(workspaceUrl);

  // open the first question
  await page.getByText(/Do you maintain a documented information security policy/).click();
  await expect(page.getByRole("button", { name: "Save answer" })).toBeVisible();

  // N/A demands a justification — the guard is real, not decorative
  await page.getByRole("button", { name: "N/A", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save answer" })).toBeDisabled();
  await expect(page.getByText(/N\/A needs a justification/)).toBeVisible();

  // answer it properly instead
  await page.getByRole("button", { name: "Yes", exact: true }).click();
  await page.getByPlaceholder(/Comment \/ how you meet this control/)
    .fill("Documented ISP, approved by the board and reviewed annually.");
  await page.getByRole("button", { name: "Save answer" }).click();

  // the close control is named by aria-label now, not by its "✕" glyph
  await page.getByRole("button", { name: "Close" }).click();
  await expect(page.getByText("1 answered")).toBeVisible();
});

test("an auditor ask and a finding both land on the question", async ({ page }) => {
  await page.goto(workspaceUrl);
  await page.getByText(/Do you maintain a documented information security policy/).click();

  // thread
  await page.getByPlaceholder(/Reply on the thread/).fill("Please share the signed ISP.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Please share the signed ISP.")).toBeVisible();

  // finding
  await page.getByRole("button", { name: /Raise a finding/ }).click();
  await page.getByPlaceholder(/Finding title/).fill("ISP not evidenced");
  await page.getByRole("button", { name: "Raise finding" }).click();
  await expect(page.getByText("ISP not evidenced")).toBeVisible();
});

test("the assessment exports as a real .xlsx (auth header attached)", async ({ page }) => {
  await page.goto(workspaceUrl);
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Export/ }).click(),
  ]);
  expect(await download.failure()).toBeNull();

  const stream = await download.createReadStream();
  const bytes = await new Promise<Buffer>((res) => {
    const chunks: Buffer[] = [];
    stream!.on("data", (c) => chunks.push(c as Buffer));
    stream!.on("end", () => res(Buffer.concat(chunks)));
  });
  // xlsx is a zip archive — "PK". A 401 JSON body would not be.
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
});

test("the audit lifecycle can actually be advanced", async ({ page }) => {
  // PATCH /assessments/{id} existed but nothing called it, so every audit was stuck in
  // "draft" forever — there was no way to submit one or close it.
  await page.goto(workspaceUrl);
  const status = page.getByLabel("Audit status");
  await expect(status).toHaveValue("draft");

  await status.selectOption("submitted");
  await expect(page.getByText("submitted", { exact: false }).first()).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Audit status"), "the change must persist")
    .toHaveValue("submitted");
});

test("the finished audit shows up on the audits list", async ({ page }) => {
  await page.goto("/audits");
  await expect(page.getByText(BANK).first()).toBeVisible();
  await expect(page.getByText(/No assessments yet/)).toHaveCount(0);
});
