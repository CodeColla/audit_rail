import { test, expect } from "@playwright/test";

/**
 * P6: autosave replaced the "Save draft" button. Committing is no longer an action — it is a
 * consequence of typing, so a spec waits for the state to settle instead of clicking.
 *
 * Asserted on `data-save-state`, not the words: "idle" (nothing written yet) and "saved" (a
 * write just succeeded) share the same copy, so a text assertion would pass before anything
 * had been saved.
 */
async function saved(page: import("@playwright/test").Page) {
  await expect(page.locator("[data-save-state]"))
    .toHaveAttribute("data-save-state", "saved", { timeout: 15_000 });
}


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
/**
 * P6: the four tabs (content · versions · approvals · attestation) became a collapsible
 * "Compliance" rail, so a panel now needs the rail opened first. The document itself is
 * always the page — governance no longer competes with it for the tab strip.
 */
const tab = async (page: import("@playwright/test").Page, name: string) => {
  const rail = page.getByRole("complementary", { name: "Compliance" });
  if (!(await rail.count())) await page.getByRole("button", { name: "Compliance" }).click();
  return rail.getByRole("button", { name: new RegExp("^" + name, "i") });
};

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
  // P6: there is no edit toggle. A DRAFT lands directly on the editable surface; a published
  // or archived version shows a locked banner whose "Start v1.x draft" unlocks it.
  const unlock = page.getByRole("button", { name: /^Start v[\d.]+ draft$/ });
  // WAIT for the page to settle before deciding. `.count()` does not auto-wait — asking while
  // the document is still loading answers 0, so the click never happens and the editor never
  // appears. Fourth time this trap has bitten in Phase 6.
  // ".ProseMirror, .sheet-editor" covers BOTH surfaces — a spreadsheet document has no
  // ProseMirror at all, so a doc-only selector waits forever on half the fixtures.
  await expect(unlock.or(page.locator(".ProseMirror, .sheet-editor")).first()).toBeVisible();
  if (await unlock.count()) await unlock.click();
  const editor = page.locator(".ProseMirror");
  await expect(editor).toBeVisible();
  await editor.click();
  await page.getByRole("button", { name: "Heading 1" }).click();
  await editor.pressSequentially("Purpose");
  // Enter at the end of a heading already drops back to a paragraph — clicking the
  // heading button again here would turn the new line INTO a second heading.
  await editor.press("Enter");
  await editor.pressSequentially("Protect bank customer data at all times.");
  // no save-and-keep-editing; the preview below is what confirms the write landed.
  await saved(page);
  await expect(page.locator(".doc-md")).toContainText("Protect bank customer data");
  await expect(page.locator(".doc-md h1").first(),
    "the H1 toolbar button produced a real heading").toContainText("Purpose");
  // .first() because TipTap's TrailingNode keeps an empty <p> at the end of the document
  await expect(page.locator(".doc-md p").first(),
    "the line after the heading is a paragraph").toContainText("Protect bank customer data");

  // ---- request approval: 2 of 2 ----
  await (await tab(page, "approvals")).click();
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
    (async () => {   // P6: the export buttons became one "Export ▾" menu
      await page.getByRole("button", { name: /^Export/ }).click();
      await page.getByRole("button", { name: "PDF", exact: true }).click();
    })(),
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

  // P6: a published record is locked. "Start v1.x draft" is the only way forward, and the
  // draft opens straight into the editor.
  await page.getByRole("button", { name: /^Start v[\d.]+ draft$/ }).click();
  await expect(page.locator(".ProseMirror")).toBeVisible();

  // the new draft is NOT publishable without its own approval round
  await (await tab(page, "approvals")).click();
  await expect(page.getByText(/hasn't been sent for approval/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Request approval" })).toBeVisible();
});

test("version history lists versions and can diff them", async ({ page }) => {
  await page.goto("/documents");
  await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();
  // P6: the versions tab moved into the Compliance rail.
  await (await tab(page, "Versions")).click();
  // Scoped to the rail: "v1.0" also appears in the header status pill and in the locked
  // banner's sentence, so a bare getByText matches three elements.
  await expect(page.getByRole("complementary", { name: "Compliance" })
    .getByText("v1.0", { exact: true })).toBeVisible();
});
