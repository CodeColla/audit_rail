import { test, expect, Page } from "@playwright/test";

/**
 * P4-S1 (Phase 5) — Evidence upload, the DataTable list toolkit, and honest previews.
 *
 * This sprint's whole reason for existing was a single line of user feedback: "the file is
 * not shown properly in queue, it works 1 time and fails like 20 times." That symptom was
 * reproduced against the real dev server (not this suite) with a 30-selection harness before
 * any fix was written, and it showed something narrower and worse than "flaky": only the
 * FIRST file selected into an open modal ever registered — `e.target.files` is a live
 * FileList, and by the time the `setSlots` updater actually ran, the input's own
 * `value = ""` reset (on the very next line of the handler) had already emptied it out from
 * under every selection after the first. The regression tests below assert queue contents
 * after REPEATED selections into one open modal — the exact shape that hid this from every
 * prior test, none of which ever called `setInputFiles` more than once on the same input.
 *
 * See docs/phase5/01-findings.md §4 for the full investigation, and 02-design.md for why
 * DataTable's search is one-directional (DataTable owns the debounce, the caller only ever
 * receives the settled value) rather than the doubly-debounced controlled-prop shape an
 * earlier draft had.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);
const buf = (s: string) => Buffer.from(s);

async function apiGet(page: Page, path: string): Promise<any> {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

async function openUpload(page: Page) {
  await page.goto("/evidence");
  await page.getByRole("button", { name: /Upload evidence/ }).click();
  await page.getByText("Choose files").waitFor();
}

const fileInput = (page: Page) => page.locator('input[type="file"]');
const queue = (page: Page) => page.locator("div.max-h-64");

test.describe("the upload race (root cause of the reported bug)", () => {
  test("repeated selections into one open modal all register, not just the first", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);

    // Five SEPARATE selections into the SAME open modal — not one setInputFiles call with
    // an array, which was already covered before this sprint and never exposed the bug.
    for (let i = 0; i < 5; i++) {
      await fileInput(page).setInputFiles({ name: `${tag}-${i}.txt`, mimeType: "text/plain", buffer: buf(`${i}`) });
      // Assert immediately, not after the whole loop — this is the exact assertion P4-S8's
      // specs never made, which is why a bug this severe shipped with a green suite.
      await expect(queue(page)).toContainText(`${tag}-${i}`);
    }
    const rowCount = await queue(page).locator("input").count();
    expect(rowCount).toBe(5);
  });

  test("removing a middle row leaves the other titles matched to their real filenames", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    for (const n of ["alpha", "bravo", "charlie", "delta"]) {
      await fileInput(page).setInputFiles({ name: `${tag}-${n}.txt`, mimeType: "text/plain", buffer: buf(n) });
      await expect(queue(page)).toContainText(`${tag}-${n}`);
    }

    // remove the 2nd row (bravo) via its own row's "✕" button
    const rows = queue(page).locator("> div");
    await rows.nth(1).getByRole("button", { name: "✕" }).click();

    const remaining = await rows.evaluateAll((els) =>
      els.map((el) => ({
        title: (el.querySelector("input") as HTMLInputElement | null)?.value,
        filename: el.querySelector(".text-txt3")?.textContent ?? "",
      })));
    expect(remaining).toHaveLength(3);
    for (const r of remaining) {
      // each surviving row's title must appear inside its OWN filename caption — an
      // index-keyed list would have let a neighbour's DOM node keep a stale title here
      expect(r.filename).toContain(r.title!);
    }
    expect(remaining.map((r) => r.title)).toEqual([`${tag}-alpha`, `${tag}-charlie`, `${tag}-delta`]);
  });

  test("all five real-world file types upload end to end and reach the vault", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    const files = [
      { name: `${tag}-a.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") },
      { name: `${tag}-b.png`, mimeType: "image/png", buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47]) },
      { name: `${tag}-c.docx`, mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: buf("x") },
      { name: `${tag}-d.doc`, mimeType: "application/msword", buffer: buf("x") },
      { name: `${tag}-e.xlsx`, mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: buf("x") },
    ];
    for (const f of files) {
      await fileInput(page).setInputFiles(f);
      await expect(queue(page)).toContainText(f.name.replace(/\.[^.]+$/, ""));
    }
    await page.getByRole("button", { name: "Upload 5 files" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const rows = await apiGet(page, "/evidence");
    for (const f of files) {
      const title = f.name.replace(/\.[^.]+$/, "");
      expect(rows.some((r: any) => r.title === title), `${title} should be in the vault`).toBeTruthy();
    }
  });
});

test.describe("drag and drop", () => {
  test("dropping a file adds it to the queue", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    const dropZone = page.getByText("Choose files").locator("..");

    const dataTransfer = await page.evaluateHandle((name) => {
      const dt = new DataTransfer();
      const file = new File(["dropped"], name, { type: "text/plain" });
      dt.items.add(file);
      return dt;
    }, `${tag}-dropped.txt`);
    await dropZone.dispatchEvent("drop", { dataTransfer });

    await expect(queue(page)).toContainText(`${tag}-dropped`);
  });
});

test.describe("oversize files", () => {
  test("an oversize file is rejected before any network request, not after", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    // 26 MB — over the 25 MB client-side check — content doesn't matter, only .size
    const big = Buffer.alloc(26 * 1024 * 1024, 1);
    await fileInput(page).setInputFiles({ name: `${tag}-big.bin`, mimeType: "application/octet-stream", buffer: big });
    await expect(queue(page)).toContainText("25 MB");

    let posted = false;
    page.on("request", (r) => { if (r.method() === "POST" && r.url().includes("/api/evidence")) posted = true; });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await page.waitForTimeout(300);
    expect(posted, "an oversize file must never be POSTed").toBeFalsy();
    await expect(queue(page)).toContainText("exceeds the 25 MB limit");
  });
});

test.describe("the list toolkit (DataTable)", () => {
  test("search filters server-side and clearing restores the full list", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    await fileInput(page).setInputFiles({ name: `${tag}-findme.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.getByPlaceholder("Search titles and notes…").fill(`${tag}-findme`);
    await expect(page.locator("tbody tr")).toHaveCount(1);

    await page.getByPlaceholder("Search titles and notes…").fill(`${tag}-zzz-no-match`);
    await expect(page.getByText(/Nothing in the vault matches/)).toBeVisible();

    await page.getByPlaceholder("Search titles and notes…").fill("");
    await expect(page.locator("tbody tr").first()).toBeVisible();
  });

  test("clicking a sortable column header re-orders the rows", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    for (const n of ["bbb", "aaa"]) {
      await fileInput(page).setInputFiles({ name: `${tag}-${n}.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") });
    }
    await page.getByRole("button", { name: "Upload 2 files" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);
    await expect(page.locator("tbody tr")).toHaveCount(2);

    await page.getByRole("button", { name: /Artifact/ }).click(); // ascending
    const ascFirst = await page.locator("tbody tr").first().textContent();
    await page.getByRole("button", { name: /Artifact/ }).click(); // descending
    const descFirst = await page.locator("tbody tr").first().textContent();
    expect(ascFirst).toContain(`${tag}-aaa`);
    expect(descFirst).toContain(`${tag}-bbb`);
  });

  test("bulk-selecting two rows and deleting removes both, and reports outcomes", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    for (const n of ["one", "two"]) {
      await fileInput(page).setInputFiles({ name: `${tag}-${n}.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") });
    }
    await page.getByRole("button", { name: "Upload 2 files" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);
    await expect(page.locator("tbody tr")).toHaveCount(2);

    const boxes = page.locator('tbody input[type="checkbox"]');
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await expect(page.getByText("2 selected")).toBeVisible();

    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Delete selected" }).click();
    await expect(page.getByText(/Nothing in the vault matches|No evidence yet/)).toBeVisible();

    const rows = await apiGet(page, "/evidence");
    expect(rows.some((r: any) => r.title.startsWith(tag))).toBeFalsy();
  });
});

test.describe("preview from the list", () => {
  test("the quick-preview thumbnail opens a modal without leaving the list", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    await fileInput(page).setInputFiles({ name: `${tag}.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);

    await page.locator(`button[title="Preview ${tag}"]`).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page).toHaveURL(/\/evidence$/); // did NOT navigate away
    await page.keyboard.press("Escape");
  });

  test("the title still navigates to the full detail page — the quick preview is additive", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    await fileInput(page).setInputFiles({ name: `${tag}.pdf`, mimeType: "application/pdf", buffer: buf("%PDF-1.4\nx\n") });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);

    await page.getByRole("button", { name: tag, exact: false }).first().click();
    await expect(page).toHaveURL(/\/evidence\/view\//);
  });
});

test.describe("honest preview messages (FilePreview.classify)", () => {
  test("a legacy .doc explains itself instead of showing a blank box", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    await fileInput(page).setInputFiles({ name: `${tag}.doc`, mimeType: "application/msword", buffer: buf("legacy") });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);

    await page.locator(`button[title="Preview ${tag}"]`).click();
    await expect(page.getByText(/legacy Word document/)).toBeVisible();
  });

  test("a HEIC photo explains itself instead of a broken image icon", async ({ page }) => {
    const tag = uniq();
    await openUpload(page);
    await fileInput(page).setInputFiles({ name: `${tag}.heic`, mimeType: "image/heic", buffer: buf("heic") });
    await page.getByRole("button", { name: /^Upload/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByPlaceholder("Search titles and notes…").fill(tag);

    await page.locator(`button[title="Preview ${tag}"]`).click();
    await expect(page.getByText(/HEIC/)).toBeVisible();
  });
});
