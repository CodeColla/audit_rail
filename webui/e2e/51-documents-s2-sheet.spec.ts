import { test, expect } from "@playwright/test";

/**
 * P5-S2 — spreadsheet documents, end to end through the real UI.
 *
 * The API side (round-trip, freeze trigger, both export paths) is proven in
 * tests/test_documents.py, tests/test_render.py and tests/test_docx_export.py. What only a
 * browser can prove is the wiring: that picking "Spreadsheet" at creation actually mounts
 * jspreadsheet-ce, that typing into cells and using its toolbar produces the JSON
 * `api/render.py`'s `parse_sheet` expects, that the read view (DocBody) renders it back as a
 * real table, and that the whole thing survives the same approve/publish/export lifecycle
 * every other document format already has.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

const tab = (page: import("@playwright/test").Page, name: string) =>
  page.getByRole("button", { name, exact: true });

/** jspreadsheet-ce renders its grid as `<table class="jss_worksheet">`, with each data cell
 * addressed by `data-x`/`data-y` (0-based) — found by dumping the live DOM, since neither
 * class is documented and an earlier guess (`.jss_worksheet table td`, treating it as a
 * container) matched nothing. */
const cell = (page: import("@playwright/test").Page, x: number, y: number) =>
  page.locator(`table.jss_worksheet td[data-x="${x}"][data-y="${y}"]`);

test("author a spreadsheet document, format cells, publish it, get a PDF and a DOCX", async ({ page }) => {
  const title = `E2E Sheet ${uniq()}`;

  // ---- create, choosing the Spreadsheet format ----
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByRole("button", { name: "Spreadsheet" }).click();
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();

  // ---- write: fill cells, bold a header, right-align a value ----
  await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
  await expect(cell(page, 0, 0)).toBeVisible();

  await cell(page, 0, 0).click();
  await page.keyboard.type("Control");
  await page.keyboard.press("Tab");
  await page.keyboard.type("Owner");
  await page.keyboard.press("Enter");
  await cell(page, 0, 1).click();
  await page.keyboard.type("MFA enforced");
  await page.keyboard.press("Tab");
  await page.keyboard.type("Alice");
  await page.keyboard.press("Enter");

  // bold the header row's first cell
  await cell(page, 0, 0).click();
  await page.locator(".jtoolbar-item[role=button]")
    .filter({ has: page.locator("i", { hasText: "format_bold" }) }).click();

  // right-align the owner value
  await cell(page, 1, 1).click();
  await page.locator(".jpicker-header")
    .filter({ has: page.locator("i", { hasText: /^format_align_/ }) }).click();
  await page.locator(".jpicker-item")
    .filter({ has: page.locator("i", { hasText: "format_align_right" }) }).click();

  await page.getByRole("button", { name: "Save draft" }).click();

  // ---- the read view renders it as a real table, not raw JSON ----
  const body = page.locator(".doc-md");
  await expect(body.locator("table")).toBeVisible();
  await expect(body).toContainText("Control");
  await expect(body).toContainText("MFA enforced");
  await expect(body).toContainText("Alice");
  const headerCell = body.locator("td", { hasText: "Control" });
  await expect(headerCell).toHaveCSS("font-weight", "700");
  const ownerValueCell = body.locator("td", { hasText: "Alice" });
  await expect(ownerValueCell).toHaveCSS("text-align", "right");
  // untouched cells must not have picked up any alignment — see SheetEditor.tsx's
  // defaultColAlign fix; a plain <td> with no inline style renders browser-default left
  const blankCell = body.locator("tr").nth(2).locator("td").first();
  await expect(blankCell).not.toHaveAttribute("style", /text-align/);

  // ---- approve (1 of 1) and publish ----
  await tab(page, "approvals").click();
  await page.getByRole("button", { name: "Request approval" }).click();
  const modal = page.getByRole("heading", { name: "Request approval" }).locator("../..");
  await modal.getByRole("checkbox").nth(0).check();
  await page.getByRole("button", { name: /Request 1 of 1 approval/ }).click();
  await page.getByRole("button", { name: "Approve" }).first().click();
  const publish = page.getByRole("button", { name: /^Publish v/ });
  await expect(publish).toBeEnabled();
  await publish.click();
  await expect(page.getByText("Published", { exact: false }).first()).toBeVisible();

  // ---- PDF export: a real PDF ----
  const [pdf] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Export PDF/ }).click(),
  ]);
  expect(await pdf.failure()).toBeNull();
  const pdfStream = await pdf.createReadStream();
  const pdfHead = await new Promise<Buffer>((res) => {
    const chunks: Buffer[] = [];
    pdfStream!.on("data", (c) => chunks.push(c as Buffer));
    pdfStream!.on("end", () => res(Buffer.concat(chunks)));
  });
  expect(pdfHead.subarray(0, 4).toString()).toBe("%PDF");

  // ---- DOCX export: a real .docx (zip) ----
  const [docx] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /Export DOCX/ }).click(),
  ]);
  expect(await docx.failure()).toBeNull();
  const docxStream = await docx.createReadStream();
  const docxHead = await new Promise<Buffer>((res) => {
    const chunks: Buffer[] = [];
    docxStream!.on("data", (c) => chunks.push(c as Buffer));
    docxStream!.on("end", () => res(Buffer.concat(chunks)));
  });
  expect(docxHead.subarray(0, 2).toString()).toBe("PK");
});

test("a published spreadsheet version cannot be edited", async ({ page }) => {
  // Same freeze-trigger guarantee every other format has (pytest already proves the DB
  // level); this proves the editor doesn't even offer a way to try.
  const title = `E2E Sheet Frozen ${uniq()}`;
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByRole("button", { name: "Spreadsheet" }).click();
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();

  await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
  await cell(page, 0, 0).click();
  await page.keyboard.type("v1");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save draft" }).click();

  await tab(page, "approvals").click();
  await page.getByRole("button", { name: "Request approval" }).click();
  const modal = page.getByRole("heading", { name: "Request approval" }).locator("../..");
  await modal.getByRole("checkbox").nth(0).check();
  await page.getByRole("button", { name: /Request 1 of 1 approval/ }).click();
  await page.getByRole("button", { name: "Approve" }).first().click();
  await page.getByRole("button", { name: /^Publish v/ }).click();
  await expect(page.getByText("Published", { exact: false }).first()).toBeVisible();

  // published -> Edit starts a fresh minor draft, not an edit of the frozen v1.0
  await page.getByRole("button", { name: /Edit → new version/ }).click();
  await expect(cell(page, 0, 0)).toBeVisible();
  await expect(cell(page, 0, 0)).toHaveText("v1");   // carried over from the published version
});

// ────────────────────────────────────────────────── the list toolkit (DataTable) on Documents

async function newDoc(page: import("@playwright/test").Page, title: string) {
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
}

test.describe("the list toolkit (DataTable) on Documents", () => {
  test("search filters server-side and clearing restores the full list", async ({ page }) => {
    const tag = uniq();
    await newDoc(page, `${tag}-findme`);
    await page.goto("/documents");

    await page.getByPlaceholder("Search titles…").fill(`${tag}-findme`);
    await expect(page.locator("tbody tr")).toHaveCount(1);

    await page.getByPlaceholder("Search titles…").fill(`${tag}-zzz-no-match`);
    await expect(page.getByText(/No documents match/)).toBeVisible();

    await page.getByPlaceholder("Search titles…").fill("");
    await expect(page.locator("tbody tr").first()).toBeVisible();
  });

  test("bulk-selecting two documents archives both (not a hard delete), restorable via Show archived", async ({ page }) => {
    // Documents' bulk action archives rather than deletes — no document-delete endpoint
    // exists (see docs/phase5/02-design.md, A2 amendment); archiving is reversible via the
    // existing per-document Restore button, unlike Evidence's bulk action, which really does
    // delete. This proves the DataTable `bulkActionCopy` override actually reaches the UI.
    const tag = uniq();
    await newDoc(page, `${tag}-one`);
    await newDoc(page, `${tag}-two`);
    await page.goto("/documents");
    await page.getByPlaceholder("Search titles…").fill(tag);
    await expect(page.locator("tbody tr")).toHaveCount(2);

    const boxes = page.locator('tbody input[type="checkbox"]');
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await expect(page.getByText("2 selected")).toBeVisible();

    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Archive selected" }).click();
    await expect(page.getByText(/No documents match|No documents here yet/)).toBeVisible();

    // reversible — "Show archived" brings both straight back, still findable by the tag
    await page.getByRole("checkbox", { name: "Show archived" }).check();
    await expect(page.locator("tbody tr")).toHaveCount(2);
    await expect(page.getByText("Archived").first()).toBeVisible();
  });
});
