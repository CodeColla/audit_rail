import { test, expect, Page } from "@playwright/test";

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
  await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
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

    await page.getByRole("button", { name: "Save draft" }).click();

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
    await page.getByRole("button", { name: "Save draft" }).click();

    await expect(page.locator(".doc-md table")).toBeVisible();
    await expect(page.locator(".doc-md th").first()).toContainText("Control");
  });

  test("script tags typed into the editor never reach the rendered page", async ({ page }) => {
    // typing "<script>" produces text, not an element — the real risk is a paste or a
    // direct API call, which pytest covers. This proves the read view does not evaluate it.
    await newDoc(page, `E2E XSS ${uniq()}`);
    const editor = await openEditor(page);
    await editor.pressSequentially('<script>window.__pwned = 1</script>ordinary text');
    await page.getByRole("button", { name: "Save draft" }).click();

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
      page.getByRole("button", { name: /Export DOCX/ }).click(),
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
});

test.describe("lifecycle", () => {
  test("a draft can be discarded, reverting to the published version", async ({ page }) => {
    await page.goto("/documents");
    await page.getByRole("cell", { name: "E2E Acceptable Use Policy" }).click();

    // specs share one worker and one database, so an earlier spec may already have left a
    // draft open on this document — accept either entry point into the editor
    await page.getByRole("button", { name: /Edit → new version|Continue editing/ }).click();
    await expect(page.locator(".ProseMirror")).toBeVisible();

    await page.getByRole("button", { name: "Discard draft" }).click();
    const modal = page.getByRole("dialog");
    await expect(modal).toContainText(/will be deleted and the document reverts/);
    await modal.getByRole("button", { name: "Discard draft" }).click();

    // back to the published document with no draft anywhere
    await expect(page.locator(".ProseMirror")).toHaveCount(0);
    await expect(page.getByText(/Draft v.* in progress/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Edit → new version/ })).toBeVisible();
  });

  test("archiving hides a document from the list, restoring brings it back", async ({ page }) => {
    const title = `E2E Archive ${uniq()}`;
    await newDoc(page, title);

    await page.getByRole("button", { name: "Archive", exact: true }).click();
    await expect(page.getByText("Archived")).toBeVisible();

    await page.goto("/documents");
    await expect(page.getByRole("cell", { name: title })).toHaveCount(0);

    // restore it through the detail page (reachable by URL even while archived)
    await page.goBack();
    await page.getByRole("button", { name: "Restore", exact: true }).click();
    await page.goto("/documents");
    await expect(page.getByRole("cell", { name: title })).toBeVisible();
  });
});
