import { test, expect, Page } from "@playwright/test";

/**
 * Smoke: every authenticated route must render its own content, raise no console
 * errors, and issue no failed API calls.
 *
 * This is deliberately the cheapest, broadest net — the login redirect-loop bug we
 * hit earlier was exactly this shape (page "loads" but the app bounces or the data
 * call 401s), and no API test can see it.
 */

type Problems = { console: string[]; failedRequests: string[] };

function watch(page: Page): Problems {
  const p: Problems = { console: [], failedRequests: [] };
  page.on("console", (m) => {
    if (m.type() === "error") p.console.push(m.text());
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) {
      p.failedRequests.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
    }
  });
  return p;
}

const ROUTES: { path: string; expect: RegExp; name: string }[] = [
  { path: "/", name: "Dashboard", expect: /dashboard/i },
  { path: "/people", name: "People", expect: /people/i },
  { path: "/audits", name: "Audits", expect: /audit/i },
  { path: "/controls", name: "Controls", expect: /control/i },
  // P4-S3: the single Registers tab strip became five sibling modules
  { path: "/risks", name: "Risks", expect: /risk/i },
  { path: "/assets", name: "Assets", expect: /asset/i },
  { path: "/data", name: "Data inventory", expect: /data/i },
  { path: "/third-parties", name: "Third parties", expect: /third part/i },
  { path: "/incidents", name: "Incidents", expect: /incident/i },
  { path: "/documents", name: "Documents", expect: /document/i },
  { path: "/evidence", name: "Evidence", expect: /evidence/i },
  { path: "/tasks", name: "Tasks", expect: /task/i },
  { path: "/reports", name: "Reports", expect: /report/i },
  { path: "/admin", name: "Admin", expect: /admin/i },
];

for (const r of ROUTES) {
  test(`smoke: ${r.name} (${r.path})`, async ({ page }) => {
    const problems = watch(page);
    await page.goto(r.path);

    // The app shell is present and we were not bounced to /login.
    // exact: true matters — the Documents page also renders a "Documents → Policies"
    // footer link, so a substring match becomes ambiguous once the page finishes loading
    // (and passes while it is still showing "Loading…"). That race made this flaky.
    await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
    expect(page.url(), "should not redirect to /login").not.toContain("/login");

    // the page rendered its own identity, not a blank shell
    await expect(page.locator("h1").first()).toBeVisible();
    await expect(page.locator("body")).toContainText(r.expect);

    // give async queries a beat to settle, then assert nothing blew up
    await page.waitForLoadState("networkidle").catch(() => {});
    expect(problems.failedRequests, `failed API calls on ${r.path}`).toEqual([]);
    expect(problems.console, `console errors on ${r.path}`).toEqual([]);
  });
}

test("smoke: sidebar navigates between sections", async ({ page }) => {
  await page.goto("/");
  // walks the whole list top to bottom — including the pinned Roles entry at the very
  // bottom, which is what catches the sidebar overflowing past the fold
  for (const name of ["People", "Controls", "Risks", "Incidents", "Documents",
                      "Evidence", "Tasks", "Roles"]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.locator("h1").first()).toBeVisible();
  }
});

test("smoke: unknown route falls back into the app, not a blank page", async ({ page }) => {
  await page.goto("/this-route-does-not-exist");
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
});
