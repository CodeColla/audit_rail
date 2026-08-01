import { test, expect, Page } from "@playwright/test";

/**
 * P5-S7 — bulk select + delete on People.
 *
 * Reported by Sumit after Phase 5 closed: "bulk select and delete in the People menu is not
 * there." It wasn't — People was the only list page with a delete affordance still rendering a
 * raw <Table>, so it never inherited DataTable's selection, select-all or bulk bar.
 *
 * The case that matters here is the MIXED one. Deleting a person is refused while anything
 * still cites them (25 FK columns point at `people`), so a bulk delete over real staff will
 * usually be partly refused — and the report has to say WHICH people, by name, or it is
 * unactionable. That is what these specs pin.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function api(page: Page, method: "post", path: string, data: any) {
  // localStorage is unreadable until the origin has been visited at least once.
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  return page.request[method](`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data });
}

test("select several people and delete them", async ({ page }) => {
  const tag = `bulk${uniq()}`;
  await page.goto("/people");
  for (const n of ["one", "two", "three"]) {
    const r = await api(page, "post", "/people",
      { full_name: `${tag} ${n}`, email: `${tag}-${n}@example.com` });
    expect(r.ok(), await r.text()).toBeTruthy();
  }

  await page.reload();
  await page.getByPlaceholder(/Search name, email/).fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(3);

  // select-all covers exactly the rows currently listed
  await page.getByLabel("Select all").check();
  await expect(page.getByText("3 selected")).toBeVisible();

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete selected" }).click();

  await expect(page.getByText("Nobody matches that search.")).toBeVisible();
  await expect(page.getByText(/could not be deleted/)).toHaveCount(0);
});

test("a person who is still referenced is refused BY NAME, and the rest still go", async ({ page }) => {
  const tag = `mix${uniq()}`;
  await page.goto("/people");
  const owner = await (await api(page, "post", "/people",
    { full_name: `${tag} owner`, email: `${tag}-owner@example.com` })).json();
  await api(page, "post", "/people",
    { full_name: `${tag} spare`, email: `${tag}-spare@example.com` });
  // risks.owner_person_id is ON DELETE RESTRICT, so this makes `owner` undeletable
  const risk = await api(page, "post", "/risks",
    { title: `${tag} risk`, owner_person_id: owner.id });
  expect(risk.ok(), await risk.text()).toBeTruthy();

  await page.reload();
  await page.getByPlaceholder(/Search name, email/).fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await page.getByLabel("Select all").check();

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete selected" }).click();

  // the report NAMES the person and says why — the whole point of the S7 DataTable change
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("1 could not be deleted");
  await expect(alert).toContainText(`${tag} owner`);
  await expect(alert).toContainText(/still referenced by 1 risk/);
  await expect(alert).not.toContainText(`${tag} spare`);

  // and the deletable one really went, while the refused one really stayed
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText(`${tag} owner`);
});

test("the People search box filters server-side", async ({ page }) => {
  // GET /people?q= has existed since P4 with no UI caller at all.
  const tag = `find${uniq()}`;
  await page.goto("/people");
  await api(page, "post", "/people",
    { full_name: `${tag} findable`, email: `${tag}@example.com`, employee_number: `EMP-${tag}` });

  await page.reload();
  const before = await page.locator("tbody tr").count();
  expect(before).toBeGreaterThan(1);

  await page.getByPlaceholder(/Search name, email/).fill(tag);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  // employee number is one of the three columns the endpoint searches
  await page.getByPlaceholder(/Search name, email/).fill(`EMP-${tag}`);
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("a Viewer gets no checkboxes at all", async ({ page, request, browser }) => {
  // The selection column is gated on can("people","delete") — a read-only user must not be
  // offered a bulk action they cannot perform. A real Viewer login in the SEEDED org, not a
  // doctored localStorage: `useCan` reads permissions the server issues, so faking them
  // client-side proves nothing (and permissions are dotted, `people.delete`, not colon'd).
  const tag = uniq();
  await page.goto("/people");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const email = `people-viewer-${tag}@example.com`;
  const made = await request.post("/api/e2e/make-member", {
    headers: { Authorization: `Bearer ${token}` },
    data: { email, full_name: "People Viewer", role_name: "Viewer", password: "Passw0rdOne" },
  });
  expect(made.ok(), await made.text()).toBeTruthy();

  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const viewer = await ctx.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(email);
  await viewer.getByLabel("Password").fill("Passw0rdOne");
  await viewer.getByRole("button", { name: "Sign in" }).click();
  // Wait for the session to actually land before navigating — going straight to /people
  // races the redirect and lands back on /login with nothing to assert against.
  await expect(viewer.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  await viewer.goto("/people");
  await expect(viewer.locator("tbody tr").first()).toBeVisible();   // can read the roster…
  await expect(viewer.getByLabel("Select all")).toHaveCount(0);     // …but cannot select
  await expect(viewer.getByLabel("Select row")).toHaveCount(0);
  await ctx.close();
});
