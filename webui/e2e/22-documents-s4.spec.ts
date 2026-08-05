import { test, expect, Page } from "@playwright/test";

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
 * P4-S4 — the rich editor, DOCX export, and the archive / discard lifecycle.
 *
 * The API side is proven in tests/test_documents.py, test_html_sanitize.py and
 * test_docx_export.py. What only a browser can prove is the wiring: that the toolbar
 * produces real structure, that sanitisation survives a round trip through the editor,
 * that the exported .docx is a file Word could open, and that the lifecycle buttons do
 * what they say.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

/** Create a document and land on its detail page. */
async function newDoc(page: Page, title: string) {
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
}

async function openEditor(page: Page) {
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
  return editor;
}

test.describe("document types", () => {
  test("the type list comes from the API and excludes the invalid STANDARD", async ({ page }) => {
    await page.goto("/documents");
    await page.getByRole("button", { name: /New document/ }).click();
    const type = page.getByLabel("Type");
    const options = await type.locator("option").allTextContents();

    // STANDARD was offered for months and 500s — the schema CHECK rejects it
    expect(options).not.toContain("Standard");
    // and the three valid types the hardcoded list forgot are now present
    for (const t of ["Register", "Template", "Soa"]) expect(options).toContain(t);
    expect(options).toContain("Policy");
  });
});

test.describe("rich editor", () => {
  test("the toolbar produces real structure, not markdown source", async ({ page }) => {
    await newDoc(page, `E2E Rich ${uniq()}`);
    const editor = await openEditor(page);

    await page.getByRole("button", { name: "Heading 2" }).click();
    await editor.pressSequentially("Scope");
    // Enter after a heading already returns to a paragraph; clicking Heading 2 again
    // here would make a second heading instead of clearing the first.
    await editor.press("Enter");
    await editor.pressSequentially("This applies to ");
    await page.getByRole("button", { name: "Bold" }).click();
    await editor.pressSequentially("everyone");
    await page.getByRole("button", { name: "Bold" }).click();
    await editor.press("Enter");
    await page.getByRole("button", { name: "Bullet list" }).click();
    await editor.pressSequentially("Lock screens");

    // structure exists inside the editor before we even save
    await expect(editor.locator("h2")).toContainText("Scope");
    await expect(editor.locator("strong")).toContainText("everyone");
    await expect(editor.locator("ul li")).toContainText("Lock screens");

    await saved(page);

    // …and survives the round trip through the sanitiser and back onto the read view
    const body = page.locator(".doc-md");
    await expect(body.locator("h2")).toContainText("Scope");
    await expect(body.locator("strong")).toContainText("everyone");
    await expect(body.locator("ul li")).toContainText("Lock screens");
  });

  test("a table can be inserted and renders as a table", async ({ page }) => {
    await newDoc(page, `E2E Table ${uniq()}`);
    const editor = await openEditor(page);
    await page.getByRole("button", { name: "Insert table" }).click();
    await editor.locator("th").first().click();
    await editor.pressSequentially("Control");
    await saved(page);

    await expect(page.locator(".doc-md table")).toBeVisible();
    await expect(page.locator(".doc-md th").first()).toContainText("Control");
  });

  test("script tags typed into the editor never reach the rendered page", async ({ page }) => {
    // typing "<script>" produces text, not an element — the real risk is a paste or a
    // direct API call, which pytest covers. This proves the read view does not evaluate it.
    await newDoc(page, `E2E XSS ${uniq()}`);
    const editor = await openEditor(page);
    await editor.pressSequentially('<script>window.__pwned = 1</script>ordinary text');
    await saved(page);

    await expect(page.locator(".doc-md")).toContainText("ordinary text");
    expect(await page.evaluate(() => (window as any).__pwned)).toBeUndefined();
    await expect(page.locator(".doc-md script")).toHaveCount(0);
  });
});

test.describe("export", () => {
  test("a version downloads as a real .docx", async ({ page }) => {
    await page.goto("/documents");
    await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        // P6: the three export buttons became one "Export ▾" menu.
        await page.getByRole("button", { name: /^Export/ }).click();
        await page.getByRole("button", { name: "DOCX", exact: true }).click();
      })(),
    ]);
    expect(await download.failure()).toBeNull();
    expect(download.suggestedFilename()).toMatch(/\.docx$/);

    const stream = await download.createReadStream();
    const bytes = await new Promise<Buffer>((res) => {
      const chunks: Buffer[] = [];
      stream!.on("data", (c) => chunks.push(c as Buffer));
      stream!.on("end", () => res(Buffer.concat(chunks)));
    });
    // "PK" — a .docx is a zip. Anything else means we downloaded an error body.
    expect(bytes.subarray(0, 2).toString()).toBe("PK");
    expect(bytes.length).toBeGreaterThan(5000);
  });

  test("titles with a semicolon, a percent sign or non-Latin text still download", async ({ page }) => {
    // Three real bugs at once: Starlette encodes response headers as latin-1 (a Devanagari
    // title 500'd); the client's filename regex stopped at the first semicolon, even one
    // legitimately inside the quoted value; and it ran the filename through
    // decodeURIComponent, which throws on a lone '%' that isn't valid percent-encoding.
    await newDoc(page, `E2E; Policy 50% ready — नीति ${uniq()}`);

    const [pdf] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        await page.getByRole("button", { name: /^Export/ }).click();
        await page.getByRole("button", { name: "PDF", exact: true }).click();
      })(),
    ]);
    expect(await pdf.failure(), "PDF export must not fail on this title").toBeNull();
    expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);

    const [docx] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        // P6: the three export buttons became one "Export ▾" menu.
        await page.getByRole("button", { name: /^Export/ }).click();
        await page.getByRole("button", { name: "DOCX", exact: true }).click();
      })(),
    ]);
    expect(await docx.failure(), "DOCX export must not fail on this title").toBeNull();
    expect(docx.suggestedFilename()).toMatch(/\.docx$/);
  });
});

test.describe("lifecycle", () => {
  test("a draft can be discarded, reverting to the published version", async ({ page }) => {
    await page.goto("/documents");
    await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();

    // specs share one worker and one database, so an earlier spec may already have left a
    // draft open on this document — accept either entry point into the editor
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
    await expect(page.locator(".ProseMirror")).toBeVisible();

    // P6: discard moved from the editor's button bar into the Compliance rail, where version
    // management lives, and is guarded by a native confirm rather than a modal.
    await page.getByRole("button", { name: "Compliance" }).click();
    const rail = page.getByRole("complementary", { name: "Compliance" });
    await rail.getByRole("button", { name: /^Versions/ }).click();
    page.once("dialog", (d) => d.accept());
    await rail.getByRole("button", { name: /^Discard draft/ }).click();
    // back to the published document with no draft anywhere
    await expect(page.locator(".ProseMirror")).toHaveCount(0);
    await expect(page.getByText(/Draft v.* in progress/)).toHaveCount(0);
    // back to the locked published record, offering a fresh draft
    await expect(page.getByRole("button", { name: /^Start v[\d.]+ draft$/ })).toBeVisible();
  });

  test("archiving hides a document from the list, restoring brings it back", async ({ page }) => {
    const title = `E2E Archive ${uniq()}`;
    await newDoc(page, title);

    await page.getByRole("button", { name: "Archive", exact: true }).click();
    await expect(page.getByText("Archived", { exact: true })).toBeVisible();

    await page.goto("/documents");
    await expect(page.getByRole("cell", { name: title })).toHaveCount(0);

    // restore it through the detail page (reachable by URL even while archived)
    await page.goBack();
    await page.getByRole("button", { name: "Restore", exact: true }).click();
    await page.goto("/documents");
    await expect(page.getByRole("cell", { name: title })).toBeVisible();
  });

  test("'Show archived' is the only way back to an archived document from the list", async ({ page }) => {
    // The API grew include_archived, but nothing in the SPA sent it — archiving a
    // document removed it from the only screen that links to its detail page, so its own
    // Restore button became permanently unreachable except by a remembered URL.
    const title = `E2E Archive Toggle ${uniq()}`;
    await newDoc(page, title);
    await page.getByRole("button", { name: "Archive", exact: true }).click();

    await page.goto("/documents");
    await expect(page.getByRole("cell", { name: title })).toHaveCount(0);

    await page.getByRole("checkbox", { name: "Show archived" }).check();
    const row = page.getByRole("cell", { name: title });
    await expect(row).toBeVisible();
    await expect(page.getByText("Archived").first()).toBeVisible();

    await row.click();
    await page.getByRole("button", { name: "Restore", exact: true }).click();
  });

  test("edits survive a reload without any save action", async ({ page }) => {
    // Replaces "switching tabs with unsaved edits warns before discarding them". There are no
    // tabs and there is no unsaved state to warn about — the guarantee autosave has to earn
    // instead is that what you typed is still there when you come back.
    const title = `P6 persist ${uniq()}`;
    await newDoc(page, title);
    await page.locator(".ProseMirror").click();
    await page.locator(".ProseMirror").pressSequentially("survives a reload");
    await saved(page);
    await page.reload();
    await expect(page.locator(".ProseMirror")).toContainText("survives a reload");
  });

  test("a draft opens straight into the editor — there is no edit toggle", async ({ page }) => {
    // Replaces "Edit works from a tab other than Content", which tested a bug in a model that
    // no longer exists: there are no tabs and no Edit button. A user with edit rights lands
    // on the editable surface, which is the whole point of the P6 redesign.
    await newDoc(page, `P6 direct ${uniq()}`);
    await expect(page.locator(".ProseMirror")).toBeVisible();
    await expect(page.getByRole("button", { name: /Continue editing|^Edit$/ })).toHaveCount(0);
    await page.locator(".ProseMirror").click();
    await page.locator(".ProseMirror").pressSequentially("typed with no click first");
    await saved(page);
  });

  test("there is no stale-export warning, because there is nothing unsaved", async ({ page }) => {
    // Replaces "exporting while editing warns the download may be stale". That warning existed
    // because a manual Save meant the editor could hold changes the export would miss.
    // Autosave removes the state the warning described, so the warning must go with it.
    await newDoc(page, `P6 nostale ${uniq()}`);
    await page.locator(".ProseMirror").click();
    await page.locator(".ProseMirror").pressSequentially("content");
    await saved(page);
    await expect(page.getByText(/unsaved changes/i)).toHaveCount(0);
  });
});
