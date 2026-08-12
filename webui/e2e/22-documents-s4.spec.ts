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

// ────────────────────────────────────────────── P6-S5: images in a policy

/** The stored version body, as the server has it — the only honest check that what the
 *  editor DISPLAYS is not what got saved. */
async function storedContent(page: Page) {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const id = page.url().split("/documents/")[1].split("/")[0];
  const detail = await page.request.get(`/api/documents/${id}`,
    { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
  return detail.open_version.content as string;
}

/**
 * The stored body, once `ready` holds.
 *
 * `saved(page)` alone is not enough whenever a test makes two changes in a row. Autosave
 * debounces, so `data-save-state` still reads "saved" from the PREVIOUS cycle while the next
 * write is queued — the assertion passes instantly and the fetch that follows reads the
 * document as it was one edit ago. Exactly the trap that bit the P6-S4 sheet specs.
 */
async function storedContentOnce(page: Page, ready: (html: string) => boolean) {
  let html = "";
  await expect.poll(async () => { html = await storedContent(page); return ready(html); },
    { message: "the edit never reached the server", timeout: 15_000 }).toBe(true);
  return html;
}

test.describe("inline images (P6-S5)", () => {
  test("an uploaded image survives a reload, and a blob URL never reaches storage",
    async ({ page }) => {
      // THE assertion of this feature. The image is displayed from an object URL, because a
      // bare <img src="/api/…"> cannot send a bearer token. If that object URL ever reached
      // `content`, autosave would PATCH `blob:http://localhost/8f3a-…` into a column whose
      // hash backs an electronic signature — meaningless in any other tab, and permanent.
      // `DocImage` stores a file id instead, so this is structurally impossible; the test is
      // here because "structurally impossible" is a claim that has to keep being true.
      await newDoc(page, `E2E Image ${uniq()}`);
      await openEditor(page);
      await page.keyboard.type("Network diagram follows.");

      await page.setInputFiles('input[aria-label="Insert image"]', "e2e/fixtures/diagram.png");
      const img = page.locator(".ProseMirror img[data-file-id]");
      await expect(img).toBeVisible();
      // it really rendered — a broken image has naturalWidth 0
      await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
        .toBeGreaterThan(0);
      const content = await storedContentOnce(page, (h) => h.includes("<img"));
      expect(content, "the canonical route URL is what gets stored")
        .toMatch(/src="\/api\/documents\/images\/[0-9a-f-]{36}"/);
      expect(content, "a blob URL must never reach a hashed, signed column")
        .not.toContain("blob:");
      expect(content, "the bytes must not be inlined either").not.toContain("data:image");

      await page.reload();
      await openEditor(page);
      const again = page.locator(".ProseMirror img[data-file-id]");
      await expect(again).toBeVisible();
      await expect.poll(() => again.evaluate((el: HTMLImageElement) => el.naturalWidth))
        .toBeGreaterThan(0);
    });

  test("no image request is ever made without authentication", async ({ page }) => {
    // `01-smoke.spec.ts` forbids any /api/ response >= 400 app-wide. An <img src="/api/…">
    // inserted into the DOM fetches immediately, with no bearer token, and 401s — which is
    // why DocBody strips the src to `data-doc-image` in the HTML STRING rather than swapping
    // it in an effect that runs too late.
    const unauthorised: string[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/api/documents/images/") && r.status() >= 400) {
        unauthorised.push(`${r.status()} ${r.url()}`);
      }
    });

    await newDoc(page, `E2E ImageAuth ${uniq()}`);
    await openEditor(page);
    await page.setInputFiles('input[aria-label="Insert image"]', "e2e/fixtures/diagram.png");
    await expect(page.locator(".ProseMirror img[data-file-id]")).toBeVisible();
    await saved(page);
    await page.reload();
    await openEditor(page);
    await expect(page.locator(".ProseMirror img[data-file-id]")).toBeVisible();

    expect(unauthorised, "an image was requested without a token").toEqual([]);
  });

  test("a pasted image uploads instead of vanishing", async ({ page }) => {
    // Nobody uses a file picker to put a screenshot in a policy. Before this, ProseMirror
    // silently dropped the paste because no image node existed in the schema.
    await newDoc(page, `E2E ImagePaste ${uniq()}`);
    await openEditor(page);

    // Build a real PNG in the page and paste it through a DataTransfer.
    await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 40; canvas.height = 30;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#f97316"; ctx.fillRect(0, 0, 40, 30);
      return new Promise<void>((resolve) => canvas.toBlob((blob) => {
        const dt = new DataTransfer();
        dt.items.add(new File([blob!], "pasted.png", { type: "image/png" }));
        document.querySelector(".ProseMirror")!
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true,
                                                       cancelable: true }));
        resolve();
      }, "image/png"));
    });

    await expect(page.locator(".ProseMirror img[data-file-id]")).toBeVisible();
    expect(await storedContentOnce(page, (h) => h.includes("<img")))
      .toMatch(/src="\/api\/documents\/images\//);
  });

  test("a published policy shows its images in the read view and exports them",
    async ({ page }) => {
      await newDoc(page, `E2E ImagePub ${uniq()}`);
      await openEditor(page);
      await page.keyboard.type("See the diagram.");
      await page.setInputFiles('input[aria-label="Insert image"]', "e2e/fixtures/diagram.png");
      await expect(page.locator(".ProseMirror img[data-file-id]")).toBeVisible();
      await saved(page);

      const docId = page.url().split("/documents/")[1].split("/")[0];
      const token = await page.evaluate(() => localStorage.getItem("ar_token"));
      const auth = { Authorization: `Bearer ${token}` };
      const detail = await page.request.get(`/api/documents/${docId}`, { headers: auth })
        .then((r) => r.json());
      const verId = detail.open_version.id;

      const pdf = await page.request.get(
        `/api/documents/${docId}/versions/${verId}/render.pdf`, { headers: auth });
      expect(pdf.status()).toBe(200);
      const bytes = await pdf.body();
      expect(bytes.subarray(0, 4).toString()).toBe("%PDF");
      // `/Subtype /Image`, not bare `/Image`: every reportlab PDF already contains the
      // latter three times over in its ProcSet array `[/PDF /Text /ImageB /ImageC /ImageI]`,
      // so that assertion passes on a document with no picture in it. Nor a byte size — a
      // 459-byte fixture moves the file by under a kilobyte. See tests/test_document_images.py.
      expect(bytes.includes(Buffer.from("/Subtype /Image")),
        "the image did not reach the PDF").toBe(true);
    });
});

test.describe("import a Word policy (P6-S5)", () => {
  test("a .docx arrives as real structure, with its pictures", async ({ page }) => {
    // The prose equivalent of the spreadsheet's "Import .xlsx". The point of using mammoth
    // rather than the already-installed docx-preview is that headings and lists arrive as
    // headings and lists — docx-preview emits <div>/<span> soup that our sanitiser would
    // flatten into an unstructured wall of text.
    await newDoc(page, `E2E Docx ${uniq()}`);
    await openEditor(page);
    await page.keyboard.type("This text is about to be replaced.");

    page.once("dialog", (d) => d.accept());          // import replaces the draft, so it asks
    await page.setInputFiles('input[aria-label="Import .docx"]',
                             "e2e/fixtures/legacy-policy.docx");

    const editor = page.locator(".ProseMirror");
    await expect(editor.locator("h1")).toContainText("Acceptable Use Policy");
    await expect(editor.locator("h2").first()).toContainText("Scope");
    await expect(editor.locator("ul li").first()).toContainText("Lock your screen");
    await expect(editor.locator("strong")).toContainText("security@example.com");
    await expect(editor).not.toContainText("This text is about to be replaced");

    // the embedded picture went through the image store rather than arriving as a data: URI
    // that the sanitiser would silently strip on the first save
    await expect(editor.locator("img[data-file-id]")).toBeVisible();

    // and the losses are stated up front, not discovered after approval
    await expect(page.getByRole("status", { name: "Import notes" }))
      .toContainText(/Not imported/);

    const content = await storedContentOnce(page, (h) => h.includes("Acceptable Use Policy"));
    expect(content).toMatch(/<h2>|<h2 /);
    expect(content).toMatch(/src="\/api\/documents\/images\//);
    expect(content, "a data: URI would be stripped on save, losing the picture")
      .not.toContain("data:image");
  });
});
