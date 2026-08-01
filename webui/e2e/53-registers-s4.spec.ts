import { test, expect, Page } from "@playwright/test";

/**
 * P5-S4 — the DataTable rolled across all five registers, the incident category column, and
 * delete safety.
 *
 * Every one of these endpoints already accepted `?q=`; not one screen sent it. Sorting and
 * row selection did not exist anywhere in the app before S1 built `DataTable`.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function api(page: Page, method: "post" | "delete", path: string, data?: unknown) {
  // localStorage only exists once the page is ON the origin — calling this before any
  // goto() reads from about:blank and throws.
  if (!page.url().startsWith("http")) await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request[method](`/api${path}`,
    { headers: { Authorization: `Bearer ${token}` }, data: data as any });
  return r;
}

const REGISTERS = [
  { path: "/risks", placeholder: "Search risks…" },
  { path: "/assets", placeholder: "Search assets…" },
  { path: "/data", placeholder: "Search data items…" },
  { path: "/third-parties", placeholder: "Search vendors…" },
  { path: "/incidents", placeholder: "Search incidents…" },
] as const;

test.describe("every register got the list toolkit", () => {
  for (const reg of REGISTERS) {
    test(`${reg.path} has search, sortable headers and row selection`, async ({ page }) => {
      await page.goto(reg.path);
      await expect(page.getByPlaceholder(reg.placeholder)).toBeVisible();
      // sortable columns render their header as a button; a plain <Table> never did
      expect(await page.locator("thead button").count(),
        "sortable column headers").toBeGreaterThan(0);
      expect(await page.locator('tbody input[type="checkbox"]').count(),
        "row-selection checkboxes").toBeGreaterThan(0);
    });
  }
});

test("search filters a register server-side", async ({ page }) => {
  const tag = `s4risk-${uniq()}`;
  await api(page, "post", "/risks", { title: tag });
  await page.goto("/risks");
  await page.getByPlaceholder("Search risks…").fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody tr").first()).toContainText(tag);

  await page.getByPlaceholder("Search risks…").fill(`${tag}-nope`);
  await expect(page.getByText(/No risks match/)).toBeVisible();
});

test("an incident carries a category, end to end", async ({ page }) => {
  // `incident_category` was seeded as a vocabulary in P4-S3 and had no column to write to
  // until S4 added `incidents.category`.
  const tag = `s4inc-${uniq()}`;
  await page.goto("/incidents");
  await page.getByRole("button", { name: /New incident/ }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("Title *").fill(tag);
  await modal.getByLabel("Category").selectOption("Phishing");
  await modal.getByRole("button", { name: /Create/ }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByPlaceholder("Search incidents…").fill(tag);
  await expect(page.locator("tbody tr").first()).toContainText("Phishing");
});

test("a refused delete is reported against its row, not swallowed", async ({ page }) => {
  // `assets.vendor_third_party_id` is ON DELETE RESTRICT, so deleting a vendor an asset
  // still names is a 409. This is the case the whole S4 ordering exists for: bulk delete
  // must surface a per-row reason rather than a wall of failures with no explanation.
  // (The equivalent risk guard — findings.risk_id — is covered in pytest, because no API
  // route populates that column, so the state cannot be built from the UI.)
  const tag = `s4v-${uniq()}`;
  const vendor = await (await api(page, "post", "/third-parties", { name: tag })).json();
  const asset = await api(page, "post", "/assets",
    { name: `${tag}-asset`, asset_type: "PHYSICAL", vendor_third_party_id: vendor.id });
  expect(asset.ok(), await asset.text()).toBeTruthy();

  await page.goto("/third-parties");
  await page.getByPlaceholder("Search vendors…").fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.locator('tbody input[type="checkbox"]').first().check();
  await expect(page.getByText("1 selected")).toBeVisible();

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete selected" }).click();

  await expect(page.getByText(/could not be deleted/)).toBeVisible();
  await expect(page.getByText(/still named on an asset/)).toBeVisible();
  // and it is still there — a refused delete must not partially apply
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("bulk-deleting rows that CAN go removes them", async ({ page }) => {
  const tag = `s4del-${uniq()}`;
  await api(page, "post", "/risks", { title: `${tag}-one` });
  await api(page, "post", "/risks", { title: `${tag}-two` });

  await page.goto("/risks");
  await page.getByPlaceholder("Search risks…").fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(2);

  const boxes = page.locator('tbody input[type="checkbox"]');
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete selected" }).click();
  await expect(page.getByText(/No risks match/)).toBeVisible();
});
