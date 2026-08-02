import { test, expect, Page } from "@playwright/test";

/**
 * P5-S8 — creating a login for an employee, from the People screen.
 *
 * Sumit: "there is a bar that shows for Person to Login we have to attach username and
 * password from the Admin side, but i dont see any menu." There wasn't one — the form pointed
 * at "Admin → Members", which has never existed, while POST /people/{id}/invite sat complete
 * and uncalled since P4-S2.
 *
 * The test that matters is the WHOLE journey: create the person with a login, then actually
 * sign in as them in a clean context and land on the forced password change. Asserting that
 * the form submits would prove nothing about whether the account works.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function addPersonWithLogin(page: Page, name: string, email: string, pw: string) {
  await page.goto("/people");
  await page.getByRole("button", { name: /Add person/ }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("Full name *").fill(name);
  await modal.getByLabel("Email *").fill(email);
  await modal.getByLabel("Give them a login").check();
  await modal.getByLabel("Temporary password").fill(pw);
  await modal.getByRole("button", { name: "Add person" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

test("create a person with a login, then sign in as them", async ({ page, browser }) => {
  const tag = uniq();
  const email = `hire-${tag}@example.com`;
  await addPersonWithLogin(page, `Hire ${tag}`, email, "Temp1234");

  // sign in as the new account, in a signed-out context
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const them = await ctx.newPage();
  await them.goto("/login");
  await them.getByLabel("Email").fill(email);
  await them.getByLabel("Password").fill("Temp1234");
  await them.getByRole("button", { name: "Sign in" }).click();

  // the temporary password is temporary: nothing else is reachable until it is replaced
  await expect(them.getByRole("heading", { name: /password/i })).toBeVisible();
  await expect(them.getByRole("link", { name: "Documents", exact: true })).toHaveCount(0);

  // exact: "New password" is a substring of "Confirm new password", and getByLabel matches
  // substrings case-insensitively — the same trap as "Every" vs "Search everything" in S7.
  await them.getByLabel("Current password", { exact: true }).fill("Temp1234");
  await them.getByLabel("New password", { exact: true }).fill("TheirOwn9Pass");
  await them.getByLabel("Confirm new password", { exact: true }).fill("TheirOwn9Pass");
  await them.getByRole("button", { name: /Change|Update|Save/ }).click();

  // …and now they are in
  await expect(them.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
  await ctx.close();
});

test("the drawer grants, re-roles and revokes an existing person's access", async ({ page }) => {
  const tag = uniq();
  const email = `later-${tag}@example.com`;
  await page.goto("/people");
  await page.getByRole("button", { name: /Add person/ }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("Full name *").fill(`Later ${tag}`);
  await modal.getByLabel("Email *").fill(email);
  await modal.getByRole("button", { name: "Add person" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByPlaceholder(/Search name, email/).fill(tag);
  // WAIT for the debounced search to actually narrow the table. Clicking straight after
  // fill() opens whoever happened to be the first row of the UNFILTERED list — which is how
  // the first draft of this spec ended up asserting against a completely different person.
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.locator("tbody tr").first().click();
  const drawer = page.getByRole("dialog");

  // grant
  await expect(drawer.getByText("No login — this is normal")).toBeVisible();
  await drawer.getByRole("button", { name: "Give them a login" }).click();
  await drawer.getByLabel("Role").selectOption({ label: "Editor" });
  await drawer.getByLabel("Temporary password").fill("Temp1234");
  await drawer.getByRole("button", { name: "Create login" }).click();
  await expect(drawer.getByText("Has a login")).toBeVisible();
  await expect(drawer.getByText(/holding the Editor role/)).toBeVisible();

  // re-role, without leaving the drawer
  await drawer.getByLabel("Role").selectOption({ label: "Viewer" });
  await expect(drawer.getByText(/holding the Viewer role/)).toBeVisible();

  // revoke — the person stays, the access goes
  page.once("dialog", (d) => d.accept());
  await drawer.getByRole("button", { name: "Remove access" }).click();
  await expect(drawer.getByText("No login — this is normal")).toBeVisible();
  await expect(drawer.getByRole("heading", { name: `Later ${tag}` })).toBeVisible();
});

test("you are never offered a button that revokes your own access", async ({ page, browser }) => {
  /**
   * Locking the organisation out of itself is the one mistake this screen must not allow.
   *
   * Built deliberately rather than reusing the signed-in fixture: the seeded admin has no
   * `people` row at all, so there is no drawer to open. Here the account is given the **Admin**
   * role, which means it genuinely holds `users.delete` — so the ONLY thing that can hide the
   * button is the "this is me" guard, which is exactly what is under test.
   */
  const tag = uniq();
  const email = `self-${tag}@example.com`;
  await page.goto("/people");
  await page.getByRole("button", { name: /Add person/ }).click();
  const modal = page.getByRole("dialog");
  await modal.getByLabel("Full name *").fill(`Self ${tag}`);
  await modal.getByLabel("Email *").fill(email);
  await modal.getByLabel("Give them a login").check();
  await modal.getByLabel("Role").selectOption({ label: "Admin" });
  await modal.getByLabel("Temporary password").fill("Temp1234");
  await modal.getByRole("button", { name: "Add person" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const them = await ctx.newPage();
  await them.goto("/login");
  await them.getByLabel("Email").fill(email);
  await them.getByLabel("Password").fill("Temp1234");
  await them.getByRole("button", { name: "Sign in" }).click();
  await them.getByLabel("Current password", { exact: true }).fill("Temp1234");
  await them.getByLabel("New password", { exact: true }).fill("TheirOwn9Pass");
  await them.getByLabel("Confirm new password", { exact: true }).fill("TheirOwn9Pass");
  await them.getByRole("button", { name: "Change password" }).click();
  await expect(them.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  await them.goto("/people");
  await them.getByPlaceholder(/Search name, email/).fill(tag);
  await them.locator("tbody tr", { hasText: `Self ${tag}` }).first().click();

  const drawer = them.getByRole("dialog");
  await expect(drawer.getByRole("heading", { name: `Self ${tag}` })).toBeVisible();
  await expect(drawer.getByText("Has a login")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Remove access" })).toHaveCount(0);
  await expect(drawer.getByText(/your own account/i)).toBeVisible();
  await ctx.close();
});

test("someone who cannot manage users is offered no login controls", async ({ page, request, browser }) => {
  const tag = uniq();
  await page.goto("/people");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const email = `editor-${tag}@example.com`;
  const made = await request.post("/api/e2e/make-member", {
    headers: { Authorization: `Bearer ${token}` },
    data: { email, full_name: "An Editor", role_name: "Editor", password: "Passw0rdOne" },
  });
  expect(made.ok(), await made.text()).toBeTruthy();

  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const editor = await ctx.newPage();
  await editor.goto("/login");
  await editor.getByLabel("Email").fill(email);
  await editor.getByLabel("Password").fill("Passw0rdOne");
  await editor.getByRole("button", { name: "Sign in" }).click();
  await expect(editor.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  await editor.goto("/people");
  await editor.getByRole("button", { name: /Add person/ }).click();
  // an Editor holds people.add but NOT users.add — they can add people, not accounts
  await expect(editor.getByRole("dialog").getByLabel("Give them a login")).toHaveCount(0);
  await ctx.close();
});
