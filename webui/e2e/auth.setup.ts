import { test as setup, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AUTH_FILE = path.join(HERE, ".auth/user.json");

/**
 * Logs in through the real form (not an API shortcut) so the login page itself is
 * covered, then saves the session for every other spec to reuse.
 *
 * The app keeps its session in localStorage (`ar_token` + `ar_user`), which
 * storageState captures — but only for an origin the page has actually visited,
 * hence the explicit navigation before saving.
 */
setup("authenticate", async ({ page }) => {
  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });

  await page.goto("/login");
  await page.getByLabel("Email").fill("sumit.t@iesglabs.com");
  await page.getByLabel("Password").fill("audit_rail");
  await page.getByRole("button", { name: "Sign in" }).click();

  // landed in the app shell, not bounced back to /login
  await expect(page).toHaveURL(/\/(?!login)/, { timeout: 15_000 });
  await expect(page.getByRole("link", { name: "Documents" })).toBeVisible();

  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  expect(token, "login should store ar_token").toBeTruthy();

  await page.context().storageState({ path: AUTH_FILE });
});
