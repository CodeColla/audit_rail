import { test, expect, Page } from "@playwright/test";

/**
 * P4-S8 — the evidence vault as a library rather than a drop box.
 *
 * tests/test_evidence_s8.py proves the API. What only a browser proves: that the batch
 * upload really issues one request per file and reports them individually, that the detail
 * page is a page (deep-linkable on a cold load, not a drawer resolved out of the list), and
 * that a LINK artifact — which is what every seeded demo row is — renders its URL instead of
 * a preview that can only 404.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);
const pdf = (s: string) => Buffer.from(`%PDF-1.4\n${s}\n`);

async function apiGet(page: Page, path: string): Promise<any> {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

async function apiPost(page: Page, path: string, data: any): Promise<any> {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.post(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

async function uploadOne(page: Page, title: string) {
  await page.goto("/evidence");
  await page.getByRole("button", { name: /Upload evidence/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: `${title}.pdf`, mimeType: "application/pdf", buffer: pdf(title) });
  await page.getByRole("button", { name: /^Upload$/ }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

test.describe("bulk upload", () => {
  test("several files upload in one pass, each named after its file", async ({ page }) => {
    const tag = uniq();
    await page.goto("/evidence");
    await page.getByRole("button", { name: /Upload evidence/ }).click();

    await page.locator('input[type="file"]').setInputFiles(["a", "b", "c"].map((n) => ({
      name: `${tag}-${n}.pdf`, mimeType: "application/pdf", buffer: pdf(n) })));
    // the title defaults to the filename WITHOUT its extension, and stays editable
    await expect(page.getByRole("dialog").locator('input[value="' + tag + '-a"]')).toBeVisible();

    await page.getByRole("button", { name: "Upload 3 files" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const rows = await apiGet(page, "/evidence");
    const mine = rows.filter((r: any) => r.title.startsWith(tag));
    expect(mine.map((r: any) => r.title).sort())
      .toEqual([`${tag}-a`, `${tag}-b`, `${tag}-c`]);
    expect(mine.every((r: any) => r.original_name?.endsWith(".pdf"))).toBeTruthy();
  });

  test("a per-file title can be overridden before uploading", async ({ page }) => {
    const tag = uniq();
    await page.goto("/evidence");
    await page.getByRole("button", { name: /Upload evidence/ }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: `${tag}-raw.pdf`, mimeType: "application/pdf", buffer: pdf("x") });

    const row = page.getByRole("dialog").locator(`input[value="${tag}-raw"]`);
    await row.fill(`Renamed ${tag}`);
    await page.getByRole("button", { name: /^Upload$/ }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const rows = await apiGet(page, "/evidence");
    expect(rows.some((r: any) => r.title === `Renamed ${tag}`)).toBeTruthy();
    expect(rows.some((r: any) => r.title === `${tag}-raw`)).toBeFalsy();
  });
});

test.describe("the detail page", () => {
  test("shows the filename, mime type and size, and survives a cold load", async ({ page }) => {
    const tag = uniq();
    await uploadOne(page, `Detail ${tag}`);
    const rows = await apiGet(page, "/evidence");
    const made = rows.find((r: any) => r.title === `Detail ${tag}`);

    // a real page on a cold load — no dialog, and no dependence on the list being fetched
    await page.goto(`/evidence/view/${made.id}`);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "← Evidence vault" })).toBeVisible();
    await expect(page.locator("h1")).toContainText(`Detail ${tag}`);
    // scoped to the metadata card: the filename also appears in the preview caption and
    // on the Download button, so an unscoped getByText is a strict-mode violation
    const meta = page.locator("div").filter({ hasText: /^Filename/ }).last();
    await expect(meta).toContainText(`Detail ${tag}.pdf`);
    await expect(page.getByText("application/pdf").first()).toBeVisible();
  });

  test("renaming from the detail page updates the vault list", async ({ page }) => {
    const tag = uniq();
    await uploadOne(page, `Before ${tag}`);
    const made = (await apiGet(page, "/evidence")).find((r: any) => r.title === `Before ${tag}`);

    await page.goto(`/evidence/view/${made.id}`);
    await page.getByRole("button", { name: "Edit details" }).click();
    await page.getByLabel("Title *").fill(`After ${tag}`);
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.locator("h1")).toContainText(`After ${tag}`);

    await page.goto("/evidence");
    await expect(page.locator("tbody tr", { hasText: `After ${tag}` })).toBeVisible();
    await expect(page.locator("tbody tr", { hasText: `Before ${tag}` })).toHaveCount(0);
  });

  test("a LINK artifact shows where it actually lives instead of a broken preview", async ({ page }) => {
    // every artifact seed_demo.py creates is medium='LINK' with no file behind it, and the
    // vault used to render a FilePreview for them — a join that can never match, so all four
    // demo rows showed a failed preview and the external_url appeared nowhere in the product
    await page.goto("/evidence");
    const rows = await apiGet(page, "/evidence");
    const link = rows.find((r: any) => r.medium === "LINK");
    test.skip(!link, "needs a seeded LINK artifact");

    await page.goto(`/evidence/view/${link.id}`);
    await expect(page.getByText("External link")).toBeVisible();
    await expect(page.getByRole("link", { name: /^https?:\/\// })).toBeVisible();
    await expect(page.getByText("Preview")).toHaveCount(0);
    await expect(page.getByText("This file could not be loaded.")).toHaveCount(0);
  });

  test("a deleted artifact deep-links to an explanation, not a blank vault", async ({ page }) => {
    await page.goto("/evidence/view/00000000-0000-0000-0000-000000000000");
    await expect(page.getByText(/no longer exists/)).toBeVisible();
  });
});

test.describe("control linkage", () => {
  test("linking from the evidence side shows on the control page too", async ({ page }) => {
    const tag = uniq();
    await uploadOne(page, `Linkable ${tag}`);
    const made = (await apiGet(page, "/evidence")).find((r: any) => r.title === `Linkable ${tag}`);
    // its OWN control: 44-controls.spec.ts attaches evidence to the first one, so reusing
    // that makes "remove the first row" remove someone else's link — green alone, red in a
    // full run, which is the worst kind of test
    const domains = await apiGet(page, "/library/domains");
    const control = await apiPost(page, "/library/controls", {
      domain_id: domains[0].id, code: `S8 ${tag}`,
      statement: `A control owned by the S8 linkage spec ${tag}.`, lifecycle: "per_audit" });
    control.code = `S8 ${tag}`;

    await page.goto(`/evidence/view/${made.id}`);
    await page.getByRole("button", { name: /Link a control/ }).click();
    await page.getByPlaceholder("Search by reference or statement…").fill(control.code);
    await page.getByRole("button", { name: new RegExp(control.code.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).click();
    await expect(page.getByText("Controls this proves · 1")).toBeVisible();

    // and the control's own page agrees
    await page.goto(`/controls/view/${control.id}`);
    await expect(page.getByText(`Linkable ${tag}`)).toBeVisible();

    // unlink from the control side, and the evidence page agrees
    await page.getByRole("button", { name: "remove" }).first().click();
    await page.goto(`/evidence/view/${made.id}`);
    await expect(page.getByText("Controls this proves · 0")).toBeVisible();
  });
});

test.describe("search", () => {
  test("the vault filters server-side and the term survives a reload", async ({ page }) => {
    const tag = uniq();
    await uploadOne(page, `Searchable ${tag}`);
    await uploadOne(page, `Unrelated ${tag}`);

    await page.goto("/evidence");
    await page.getByPlaceholder("Search titles and notes…").fill(`Searchable ${tag}`);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.locator("tbody tr")).toContainText(`Searchable ${tag}`);

    await page.getByPlaceholder("Search titles and notes…").fill(`zzz${tag}`);
    await expect(page.getByText(/Nothing in the vault matches/)).toBeVisible();

    // clearing restores the full list — a filtered result must never be cached as "the vault"
    await page.getByPlaceholder("Search titles and notes…").fill("");
    await expect(page.locator("tbody tr").first()).toBeVisible();
    expect(await page.locator("tbody tr").count()).toBeGreaterThan(1);
  });

  test("searching a picker does not truncate the vault page", async ({ page }) => {
    // THE cache-key regression. Six components read ["evidence", …]; if a picker wrote its
    // filtered result under a bare ["evidence"], typing here would silently shrink the list
    // page and every other picker.
    const tag = uniq();
    await uploadOne(page, `Picker ${tag}`);
    await page.goto("/evidence");
    const before = await page.locator("tbody tr").count();

    const control = (await apiGet(page, "/library/controls"))[0];
    await page.goto(`/controls/view/${control.id}`);
    // the control page has two "＋ Attach" buttons, evidence and documents; the first is
    // the evidence one (same ordering 44-controls.spec.ts relies on)
    await page.getByRole("button", { name: /Attach/ }).first().click();
    await page.getByPlaceholder("Search the vault…").fill(`Picker ${tag}`);
    await expect(page.getByRole("button", { name: /Picker/ })).toBeVisible();

    await page.goto("/evidence");
    expect(await page.locator("tbody tr").count()).toBe(before);
  });
});
