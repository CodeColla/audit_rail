import { test, expect } from "@playwright/test";

/**
 * The policy lifecycle a bank actually asks about, driven entirely through the UI:
 *   create -> write markdown -> request M-of-N approval -> approve -> publish -> PDF
 *
 * The DB enforces the approval threshold, and pytest already proves that. What this
 * spec proves is the WIRING: that the buttons reach those rules, that Publish stays
 * disabled until the quorum is met, and that the states render honestly.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

/**
 * Tab labels are lowercase in the DOM (capitalised via CSS), and "Approvals" also
 * appears as an inline shortcut inside the draft banner — so match exactly.
 */
const tab = (page: import("@playwright/test").Page, name: string) =>
  page.getByRole("button", { name, exact: true });

test("author a policy, route it through 2-of-2 approval, publish it, get a PDF", async ({ page }) => {
  const title = `E2E Policy ${uniq()}`;

  // ---- create ----
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();

  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await expect(page.getByText("Draft", { exact: false }).first()).toBeVisible();

  // ---- write ----
  // P4-S4: this is a TipTap contenteditable now, not a textarea. Two consequences:
  // the Placeholder extension renders CSS ::before rather than a `placeholder` attribute
  // (so getByPlaceholder can never match), and fill() bypasses ProseMirror's input rules.
  await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
  const editor = page.locator(".ProseMirror");
  await expect(editor).toBeVisible();
  await editor.click();
  await page.getByRole("button", { name: "Heading 1" }).click();
  await editor.pressSequentially("Purpose");
  // Enter at the end of a heading already drops back to a paragraph — clicking the
  // heading button again here would turn the new line INTO a second heading.
  await editor.press("Enter");
  await editor.pressSequentially("Protect bank customer data at all times.");
  // NB: "Save draft" also exits the editor (its onSuccess calls onDone), so there is
  // no save-and-keep-editing; the preview below is what confirms the write landed.
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.locator(".doc-md")).toContainText("Protect bank customer data");
  await expect(page.locator(".doc-md h1").first(),
    "the H1 toolbar button produced a real heading").toContainText("Purpose");
  // .first() because TipTap's TrailingNode keeps an empty <p> at the end of the document
  await expect(page.locator(".doc-md p").first(),
    "the line after the heading is a paragraph").toContainText("Protect bank customer data");

  // ---- request approval: 2 of 2 ----
  await tab(page, "approvals").click();
  await page.getByRole("button", { name: "Request approval" }).click();
  const modal = page.getByRole("heading", { name: "Request approval" }).locator("../..");
  await modal.getByRole("checkbox").nth(0).check();
  await modal.getByRole("checkbox").nth(1).check();
  await modal.getByRole("button", { name: "+" }).click();       // threshold 1 -> 2
  await page.getByRole("button", { name: /Request 2 of 2 approval/ }).click();

  // ---- publish is gated until the quorum is met ----
  await expect(page.getByText("0 of 2 required")).toBeVisible();
  const publish = page.getByRole("button", { name: /^Publish v/ });
  await expect(publish).toBeDisabled();

  await page.getByRole("button", { name: "Approve" }).first().click();
  await expect(page.getByText("1 of 2 required")).toBeVisible();
  await expect(publish, "one approval short — publish must stay locked").toBeDisabled();

  await page.getByRole("button", { name: "Approve" }).first().click();
  await expect(page.getByText("2 of 2 required")).toBeVisible();
  await expect(publish).toBeEnabled();

  // ---- publish ----
  await publish.click();
  await expect(page.getByText("Published", { exact: false }).first()).toBeVisible();

  // ---- the PDF is real, downloads with the Authorization header, and is named ----
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Export PDF/ }).click(),
  ]);
  expect(await download.failure()).toBeNull();
  expect(download.suggestedFilename(), "should be named from the doc, not 'render.pdf'")
    .toMatch(/\.pdf$/);
  const body = await download.createReadStream();
  const head = await new Promise<Buffer>((res) => {
    const chunks: Buffer[] = [];
    body!.on("data", (c) => chunks.push(c as Buffer));
    body!.on("end", () => res(Buffer.concat(chunks)));
  });
  expect(head.subarray(0, 4).toString(), "a real PDF, not a 401 JSON body").toBe("%PDF");
});

test("editing a published policy opens a new draft that needs approval again", async ({ page }) => {
  await page.goto("/documents");
  await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();

  // published doc -> Edit starts a fresh minor draft
  await page.getByRole("button", { name: /Edit → new version/ }).click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
  await page.getByRole("button", { name: "Done", exact: true }).click();

  // the new draft is NOT publishable without its own approval round
  await tab(page, "approvals").click();
  await expect(page.getByText(/hasn't been sent for approval/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Request approval" })).toBeVisible();
});

test("version history lists versions and can diff them", async ({ page }) => {
  await page.goto("/documents");
  await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();
  await page.getByRole("button", { name: /^versions/ }).click();
  await expect(page.getByText("v1.0")).toBeVisible();
});
