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
  // `:visible` matters once a workbook has more than one sheet: jspreadsheet renders EVERY
  // worksheet's table and hides the inactive ones, so an unscoped selector matches the same
  // address in each of them and trips strict mode (or silently reads the wrong grid).
  page.locator(`table.jss_worksheet:visible td[data-x="${x}"][data-y="${y}"]`);

/** Open a fresh spreadsheet document and land in its editor. */
async function newSheet(page: import("@playwright/test").Page, title: string) {
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByRole("button", { name: "Spreadsheet" }).click();
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();
  await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
  await page.locator("table.jss_worksheet").waitFor();
}

// ────────────────────────────────────────────── P5-S2b: RENDERING regressions
//
// These assert MEASURED, COMPUTED values — never markup. That distinction is the whole
// lesson of S2b: 120 specs passed while the editor was visibly broken, because the DOM is
// byte-identical whether or not the icon webfont loaded (`<i class="material-icons">undo</i>`
// has the same textContent either way), and nothing ever measured a rendered width.
// See docs/phase5/05-s2b-findings.md.

test.describe("rendering (P5-S2b)", () => {
  test("the Material Icons webfont actually loads, so the toolbar shows glyphs not words", async ({ page }) => {
    await newSheet(page, `E2E Sheet Font ${uniq()}`);

    // Neither jspreadsheet-ce nor jsuites ships this font — they only assume it exists.
    // We self-host it (material-icons npm pkg); without it every button rendered its raw
    // ligature name and the toolbar overflowed. document.fonts is the only honest check.
    await expect
      .poll(() => page.evaluate(() => document.fonts.check('24px "Material Icons"')),
            { message: "Material Icons webfont must be loaded", timeout: 10_000 })
      .toBe(true);

    const icon = page.locator(".jtoolbar i.material-icons").first();
    await expect(icon).toHaveCSS("font-family", '"Material Icons"');

    // A rendered glyph is a ~24px square. An unrendered ligature ("format_align_left")
    // is many times wider — this is the assertion that would have caught the bug.
    const w = await icon.evaluate((el) => el.getBoundingClientRect().width);
    expect(w, "an icon box, not a word").toBeLessThan(40);

    // …and the toolbar as a whole must fit, rather than overflowing its container.
    const bar = await page.locator(".jss_toolbar").evaluate((el) => ({
      scroll: el.scrollWidth, client: el.clientWidth }));
    expect(bar.scroll, "toolbar must not overflow").toBeLessThanOrEqual(bar.client + 2);
  });

  test("the grid fills its container and scrolls horizontally", async ({ page }) => {
    await newSheet(page, `E2E Sheet Width ${uniq()}`);

    const m = await page.evaluate(() => {
      const q = (s: string) => document.querySelector(s) as HTMLElement | null;
      const w = (e: HTMLElement | null) => (e ? e.getBoundingClientRect().width : 0);
      const content = q(".sheet-editor .jss_content");
      return {
        wrapper: w(q(".sheet-editor")),
        content: w(content),
        table: w(q(".sheet-editor table.jss_worksheet")),
        overflowX: content ? getComputedStyle(content).overflowX : "",
      };
    });

    // Assert the OUTCOME a user sees, not the cascade that produces it — an earlier version
    // of this test asserted `display: block` on .jss_container and passed even with the fix
    // reverted, because jsuites' `jtabs-selected` sets that anyway. Width is what was broken
    // (the grid opened at ~650px inside a ~1172px column); width is what to measure.
    expect(m.content, "the grid must fill its container").toBeGreaterThan(m.wrapper * 0.95);

    // `minDimensions` makes the grid genuinely wide, and `tableWidth` is what enables the
    // horizontal-overflow branch at all — without it, columns past the edge were unreachable.
    expect(m.table, "grid should extend past the viewport").toBeGreaterThan(m.content);
    expect(m.overflowX, "…and therefore must be scrollable").toBe("auto");
  });
});

// ────────────────────────────────────────────── P5-S2c: fullscreen, wrap, filters
//
// Measured, never markup-asserted. The fullscreen bugs were invisible to structural specs:
// the toolbar was present in the DOM the whole time, just painted over by the app header.

test.describe("fullscreen (P5-S2c)", () => {
  // 1080p, because the bottom gap only appears once the viewport is taller than the grid —
  // at Playwright's default 720 the grid already overflows and the bug does not reproduce.
  test.use({ viewport: { width: 1920, height: 1080 } });

  const enterFullscreen = (page: import("@playwright/test").Page) =>
    page.locator(".jtoolbar-item[role=button]")
      .filter({ has: page.locator("i", { hasText: /^fullscreen$/ }) }).click();

  test("Save draft and Exit stay reachable while fullscreen covers the app", async ({ page }) => {
    // Fullscreen hides DocumentDetail's own Save/Done, so the sheet renders its own bar.
    // Grouped as a named toolbar because the page already has a "Save draft" button —
    // without that, two identically-labelled buttons sit in the accessibility tree.
    await newSheet(page, `E2E Sheet FSBar ${uniq()}`);
    const bar = page.getByRole("toolbar", { name: "Fullscreen sheet actions" });
    await expect(bar, "no bar outside fullscreen").toHaveCount(0);

    await cell(page, 0, 0).click();
    await page.keyboard.type("hello");
    await page.keyboard.press("Enter");
    await enterFullscreen(page);

    await expect(bar).toBeVisible();
    await expect(bar.getByText("Unsaved changes")).toBeVisible();
    // it must be ABOVE the fullscreen box, not behind it
    expect(await bar.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
    }), "the bar must be clickable, not painted over").toBe(true);

    await bar.getByRole("button", { name: "Save draft" }).click();
    // saving from here must NOT eject you — the page's Save closes the editor, which from a
    // full-screen grid would dump the user back on the read view mid-session
    await expect(page.locator(".fullscreen"), "still fullscreen after saving").toHaveCount(1);
    await expect(bar.getByText("Unsaved changes")).toHaveCount(0);

    await bar.getByRole("button", { name: "Exit fullscreen" }).click();
    await expect(page.locator(".fullscreen")).toHaveCount(0);
    // exiting via jspreadsheet's own button keeps its glyph in step; calling the API
    // directly would leave the toolbar still showing "fullscreen_exit"
    await expect(page.locator(".jtoolbar-item i", { hasText: /^fullscreen$/ }).first()).toBeVisible();
  });

  test("the grid fills the screen and nothing is painted over the toolbar", async ({ page }) => {
    await newSheet(page, `E2E Sheet FS ${uniq()}`);
    await enterFullscreen(page);
    await expect(page.locator(".fullscreen")).toBeVisible();

    const m = await page.evaluate(() => {
      const q = (s: string) => document.querySelector(s) as HTMLElement | null;
      const content = q(".fullscreen .jss_content")!;
      const table = q(".fullscreen table.jss_worksheet")!;
      const tb = q(".fullscreen .jss_toolbar")!;
      const r = tb.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return {
        contentH: content.getBoundingClientRect().height,
        tableH: table.getBoundingClientRect().height,
        tableW: table.getBoundingClientRect().width,
        viewportW: window.innerWidth,
        toolbarOnTop: tb.contains(hit),
        hitClass: hit ? String((hit as HTMLElement).className) : "",
      };
    });

    // B1: the grid must reach the bottom. It used to stop 186px short at this viewport,
    // because 30 rows of ~27px cannot fill a 998px content area.
    expect(m.tableH, "grid must fill the fullscreen height").toBeGreaterThanOrEqual(m.contentH);
    // …and the right edge, which the first fix missed: 12 cols x 100px left ~700px blank.
    expect(m.tableW, "grid must fill the fullscreen width").toBeGreaterThan(m.viewportW * 0.9);

    // B2: the app header (sticky, z-30) used to paint over the sheet toolbar (z-21).
    // elementFromPoint is the only assertion that actually proves nothing overlaps it —
    // the toolbar was always present in the DOM, which is why markup specs stayed green.
    expect(m.toolbarOnTop, `something is covering the toolbar: ${m.hitClass}`).toBe(true);
  });
});

test.describe("wrap text and filters (P5-S2c)", () => {
  test("wrapping a column survives editing that column, and reaches the read view", async ({ page }) => {
    // Per COLUMN, not per cell: a per-cell white-space set via setStyle is wiped by
    // jspreadsheet's own updateCell on the next value change (measured in a browser), so a
    // per-cell toggle would appear to work and then silently lose the setting.
    await newSheet(page, `E2E Sheet Wrap ${uniq()}`);
    const LONG = "A deliberately long sentence that has to wrap over several lines to fit.";

    await cell(page, 0, 0).click();
    await page.keyboard.type(LONG);
    await page.keyboard.press("Enter");
    await expect(cell(page, 0, 0)).toHaveCSS("white-space", "nowrap");
    const before = await cell(page, 0, 0).evaluate((e) => e.getBoundingClientRect().height);

    await cell(page, 0, 0).click();
    await page.locator(".jtoolbar-item[role=button]")
      .filter({ has: page.locator("i", { hasText: "wrap_text" }) }).click();
    await expect(cell(page, 0, 0)).toHaveCSS("white-space", "pre-wrap");
    const after = await cell(page, 0, 0).evaluate((e) => e.getBoundingClientRect().height);
    expect(after, "a wrapped cell must get taller").toBeGreaterThan(before);

    // the point of per-column: editing the cell must NOT undo the wrap
    await cell(page, 0, 0).click();
    await page.keyboard.type(LONG + " Edited.");
    await page.keyboard.press("Enter");
    await expect(cell(page, 0, 0), "wrap must survive an edit").toHaveCSS("white-space", "pre-wrap");

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md table")).toBeVisible();
    await expect(page.locator(".doc-md td").first()).toHaveCSS("white-space", "pre-wrap");

    const token = await page.evaluate(() => localStorage.getItem("ar_token"));
    const id = page.url().split("/documents/")[1];
    const detail = await page.request.get(`/api/documents/${id}`,
      { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
    const sheet = JSON.parse(detail.open_version.content).sheets[0];
    expect(sheet.colWrap[0]).toBe(true);
    // trailing blank rows/columns are trimmed, so one populated cell stores as 1x1 rather
    // than the 20x60 grid the editor displays
    expect(sheet.data.length).toBe(1);
    expect(sheet.data[0].length).toBe(1);
  });

  test("every column offers a filter", async ({ page }) => {
    await newSheet(page, `E2E Sheet Filter ${uniq()}`);
    // Safe to enable: getData reads options.data, not the filtered `results` array, so
    // filtering the view can never persist only the visible rows.
    await expect(page.locator(".jss_filter, .jss_column_filter").first()).toBeVisible();
  });
});

// ────────────────────────────────────────────── P5-S2b: the v2 workbook format

test.describe("formulas and the v2 format", () => {
  test("a formula computes, and BOTH its value and its source are stored", async ({ page }) => {
    // The heart of the S2b format decision. `data` must hold the COMPUTED value, because
    // api/render.py and api/docx_export.py are Python and have no formula engine — if the
    // source were stored there, every PDF and Word export would print "=SUM(A1:A2)".
    // `formulas` holds the source so the editor can restore it on reopen.
    await newSheet(page, `E2E Sheet Formula ${uniq()}`);

    await cell(page, 0, 0).click();
    await page.keyboard.type("10"); await page.keyboard.press("Enter");
    await page.keyboard.type("20"); await page.keyboard.press("Enter");
    await page.keyboard.type("=SUM(A1:A2)"); await page.keyboard.press("Enter");
    await expect(cell(page, 0, 2)).toHaveText("30");

    await cell(page, 0, 0).click();
    await page.locator(".jtoolbar-item[role=button]")
      .filter({ has: page.locator("i", { hasText: "format_bold" }) }).click();
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md table")).toBeVisible();

    const token = await page.evaluate(() => localStorage.getItem("ar_token"));
    const id = page.url().split("/documents/")[1];
    const detail = await page.request.get(`/api/documents/${id}`,
      { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
    const book = JSON.parse(detail.open_version.content);

    expect(book.version).toBe(2);
    const sheet = book.sheets[0];
    expect(sheet.data.slice(0, 3).map((r: string[]) => r[0]),
      "data must hold computed values, not formula text").toEqual(["10", "20", "30"]);
    expect(sheet.formulas.A3, "the source must survive for the editor").toBe("=SUM(A1:A2)");
    expect(sheet.style.A1.bold).toBe(true);

    // the read view shows the number, never the expression
    await expect(page.locator(".doc-md")).toContainText("30");
    await expect(page.locator(".doc-md")).not.toContainText("=SUM");
  });

  test("reopening a saved sheet restores the formula, not just its value", async ({ page }) => {
    await newSheet(page, `E2E Sheet Reopen ${uniq()}`);
    await cell(page, 0, 0).click();
    await page.keyboard.type("7"); await page.keyboard.press("Enter");
    await page.keyboard.type("=A1*3"); await page.keyboard.press("Enter");
    await expect(cell(page, 0, 1)).toHaveText("21");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md table")).toBeVisible();

    // back into the editor: the cell still shows 21, and it is still a live formula —
    // editing A1 must recalculate it rather than leaving a frozen number behind.
    await page.getByRole("button", { name: /Continue editing|Edit/ }).first().click();
    await page.locator("table.jss_worksheet").waitFor();
    await expect(cell(page, 0, 1)).toHaveText("21");
    await cell(page, 0, 0).click();
    await page.keyboard.type("10"); await page.keyboard.press("Enter");
    await expect(cell(page, 0, 1), "the formula is live, not a frozen value").toHaveText("30");
  });

  test("a workbook can hold more than one worksheet", async ({ page }) => {
    await newSheet(page, `E2E Sheet Tabs ${uniq()}`);
    await cell(page, 0, 0).click();
    await page.keyboard.type("on sheet one"); await page.keyboard.press("Enter");

    // the "+" in the tab bar adds a worksheet
    await page.locator(".jtabs-add").click();
    await expect(page.locator(".jtabs-headers > div").filter({ hasText: /Sheet/ }))
      .toHaveCount(2);

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md").first()).toBeVisible();

    const token = await page.evaluate(() => localStorage.getItem("ar_token"));
    const id = page.url().split("/documents/")[1];
    const detail = await page.request.get(`/api/documents/${id}`,
      { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
    const book = JSON.parse(detail.open_version.content);
    expect(book.sheets.length, "both worksheets must persist").toBe(2);

    // a multi-sheet document titles each worksheet in the read view
    await expect(page.locator(".doc-md h2").first()).toBeVisible();
  });
});

// ────────────────────────────────────────────── P5-S2b: .xlsx import / export

test.describe("xlsx", () => {
  test("an .xlsx imports its values, formulas and sheets into the editor", async ({ page }) => {
    // Jspreadsheet CE cannot read .xlsx (paid tier), so this goes through SheetJS, which the
    // app already ships for evidence previews. See 04-spreadsheet-library-evaluation.md.
    await newSheet(page, `E2E Sheet Import ${uniq()}`);
    await page.locator('.sheet-editor input[type="file"]')
      .setInputFiles("e2e/fixtures/import-sample.xlsx");

    await expect(cell(page, 0, 0)).toHaveText("Control");
    await expect(cell(page, 0, 1)).toHaveText("MFA");
    // the imported formula is live, not a pasted number
    await expect(cell(page, 1, 3)).toHaveText("2000");

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md table").first()).toBeVisible();

    const token = await page.evaluate(() => localStorage.getItem("ar_token"));
    const id = page.url().split("/documents/")[1];
    const detail = await page.request.get(`/api/documents/${id}`,
      { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json());
    const book = JSON.parse(detail.open_version.content);
    expect(book.sheets.length, "both worksheets import").toBe(2);
    expect(book.sheets[0].name).toBe("Imported");
    expect(book.sheets[0].formulas.B4).toContain("SUM");
    expect(book.sheets[0].data[1][1], "computed value stored, not the formula").toBe("1200");
  });

  test("a spreadsheet downloads as a real .xlsx, and a policy cannot", async ({ page }) => {
    await newSheet(page, `E2E Sheet Xlsx ${uniq()}`);
    await cell(page, 0, 0).click();
    await page.keyboard.type("Item"); await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator(".doc-md table")).toBeVisible();

    const [dl] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /Export XLSX/ }).click(),
    ]);
    expect(await dl.failure()).toBeNull();
    expect(dl.suggestedFilename()).toMatch(/\.xlsx$/);
    const stream = await dl.createReadStream();
    const bytes = await new Promise<Buffer>((res) => {
      const chunks: Buffer[] = [];
      stream!.on("data", (c) => chunks.push(c as Buffer));
      stream!.on("end", () => res(Buffer.concat(chunks)));
    });
    expect(bytes.subarray(0, 2).toString(), "a .xlsx is a zip").toBe("PK");

    // a prose document offers no XLSX button at all
    await page.goto("/documents");
    await page.getByRole("button", { name: /New document/ }).click();
    await page.getByPlaceholder("Information Security Policy").fill(`E2E Prose ${uniq()}`);
    await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
    await page.getByRole("button", { name: /Create & write/ }).click();
    await expect(page.getByRole("button", { name: /Export DOCX/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Export XLSX/ })).toHaveCount(0);
  });
});

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
  // Untouched cells must not pick up an explicit alignment — see SheetEditor.tsx's
  // `defaultColAlign` note: getStyle() reports "text-align" for EVERY cell, so recording it
  // blindly would mark the whole grid as deliberately aligned.
  //
  // Asserted as "exactly one aligned cell in the table" rather than by poking a specific
  // blank row: since P5-S2c the editor trims trailing blank rows/columns on save, so there
  // is no spare empty row to inspect — and this phrasing states the actual invariant anyway.
  await expect(body.locator("td[style*='text-align']"),
    "only the one cell we right-aligned may carry an alignment").toHaveCount(1);
  await expect(body.locator("td[style*='text-align: left']")).toHaveCount(0);

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
