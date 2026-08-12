import { test, expect } from "@playwright/test";

/**
 * The attestation round trip — the one flow that genuinely needs a browser:
 * an admin starts a campaign, and a field engineer WITH NO LOGIN opens the magic
 * link in a clean context and signs. Everything here is UI-driven, no API calls.
 *
 * Seed provides: a PUBLISHED "E2E Acceptable Use Policy" whose audience is the
 * "Field Ops" department, which contains exactly one person ("E2E Engineer").
 */

const DOC = "E2E Acceptable Use Policy";

async function openAttestationTab(page: import("@playwright/test").Page) {
  await page.goto("/documents");
  await page.getByRole("cell", { name: DOC }).click();
  await expect(page.getByRole("heading", { name: DOC })).toBeVisible();
  // P6: attestation moved from a page tab into the Compliance rail.
  await page.getByRole("button", { name: "Compliance" }).click();
  await page.getByRole("complementary", { name: "Compliance" })
    .getByRole("button", { name: /^Attest/i }).click();
}

test("campaign issues a link, and the coverage bar reflects reality", async ({ page }) => {
  await openAttestationTab(page);

  // the audience resolves to the one Field Ops person, none signed yet
  await expect(page.getByText(/Currently targeting/)).toContainText("1");
  await expect(page.getByText("0 of 1 signed")).toBeVisible();

  await page.getByRole("button", { name: /Start \/ resend campaign/ }).click();

  // the links modal lists exactly one recipient with a usable /sign/ URL
  const modal = page.getByRole("heading", { name: /signing link/ }).locator("..").locator("..");
  await expect(modal).toContainText("E2E Engineer");
  await expect(modal).toContainText("/sign/");
});

test("a person with no login can open the link logged out and sign it", async ({ page, browser }) => {
  await openAttestationTab(page);
  await page.getByRole("button", { name: /Start \/ resend campaign/ }).click();

  // grab the real link the UI rendered
  const linkText = await page.locator("text=/\\/sign\\//").first().textContent();
  const signUrl = (linkText ?? "").trim();
  expect(signUrl, "UI should render a /sign/ URL").toContain("/sign/");

  // ---- a completely clean browser: no token, no user, nothing ----
  const anon = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const anonPage = await anon.newPage();
  await anonPage.goto(signUrl);

  // the policy renders for someone who has never logged in
  await expect(anonPage.getByRole("heading", { name: DOC })).toBeVisible();
  await expect(anonPage.locator("body")).toContainText("Lock your screen");
  await expect(anonPage.locator("body")).toContainText(/read and understood/i);

  // it must NOT have bounced to the login page
  expect(anonPage.url()).toContain("/sign/");
  const token = await anonPage.evaluate(() => localStorage.getItem("ar_token"));
  expect(token, "the public page must not require or create a session").toBeNull();

  // sign: the button stays disabled until both consent and name are given
  const signBtn = anonPage.getByRole("button", { name: "Sign", exact: true });
  await expect(signBtn).toBeDisabled();
  await anonPage.getByRole("checkbox").check();
  await anonPage.getByPlaceholder(/Type your full name/i).fill("E2E Engineer");
  await expect(signBtn).toBeEnabled();
  await signBtn.click();

  await expect(anonPage.getByRole("heading", { name: /Signed/i })).toBeVisible();

  // ---- reusing the same link is refused (single-use) ----
  const anon2 = await anon.newPage();
  await anon2.goto(signUrl);
  await expect(anon2.locator("body")).toContainText(/can’t be used|already been used|no longer valid/i);
  await anon.close();

  // ---- back in the app, coverage now shows the signature ----
  await openAttestationTab(page);
  await expect(page.getByText(/1 of 1 signed/)).toBeVisible();
  await expect(page.getByText("100%")).toBeVisible();
  await expect(page.getByText("signed", { exact: false }).first()).toBeVisible();
});

test("an invalid signing token is refused, not crashed", async ({ browser }) => {
  const anon = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const p = await anon.newPage();
  await p.goto("/sign/definitely-not-a-real-token");
  await expect(p.locator("body")).toContainText(/can’t be used|not valid/i);
  await anon.close();
});

test("a logged-out signer sees the policy's images (P6-S5)", async ({ page, browser }) => {
  // The signing page is deliberately unauthenticated — bare `fetch`, no axios, no bearer
  // token — so a field engineer with no account can read what they are attesting to. That
  // makes images a real problem: the app's normal image path is a blob fetch through the
  // axios instance, whose 401 interceptor would bounce the signer to /login and destroy the
  // flow. The server therefore rewrites each src to the token-scoped public route, so this
  // page needs no image code at all and a plain <img> just works.
  const title = `E2E SignImage ${Math.random().toString(36).slice(2, 7)}`;
  await page.goto("/documents");
  await page.getByRole("button", { name: /New document/ }).click();
  await page.getByPlaceholder("Information Security Policy").fill(title);
  await page.getByLabel("Owner *").selectOption({ label: "E2E Owner" });
  await page.getByRole("button", { name: /Create & write/ }).click();
  await expect(page.locator(".ProseMirror")).toBeVisible();
  await page.locator(".ProseMirror").click();
  await page.keyboard.type("Read the network diagram before signing.");
  await page.setInputFiles('input[aria-label="Insert image"]', "e2e/fixtures/diagram.png");
  await expect(page.locator(".ProseMirror img[data-file-id]")).toBeVisible();
  await expect(page.locator("[data-save-state]"))
    .toHaveAttribute("data-save-state", "saved", { timeout: 15_000 });

  // publish it, target the seeded Field Ops audience, issue a link
  const docId = page.url().split("/documents/")[1].split("/")[0];
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const auth = { Authorization: `Bearer ${token}` };
  const detail = await page.request.get(`/api/documents/${docId}`, { headers: auth })
    .then((r) => r.json());
  const verId = detail.open_version.id;
  const owner = detail.owner.id;
  await page.request.post(`/api/documents/${docId}/versions/${verId}/submit`,
    { headers: auth, data: { threshold_required: 1, approver_person_ids: [owner] } });
  const appr = await page.request.get(`/api/documents/${docId}`, { headers: auth })
    .then((r) => r.json());
  await page.request.post(`/api/documents/approvals/${appr.approval.id}/decide`,
    { headers: auth, data: { approver_person_id: owner, state: "APPROVED" } });
  await page.request.post(`/api/documents/${docId}/versions/${verId}/publish`, { headers: auth });
  await page.request.post(`/api/documents/${docId}/audiences`,
    { headers: auth, data: { rules: [{ rule: "DEPARTMENT", value: "Field Ops" }] } });
  const camp = await page.request.post(`/api/documents/${docId}/attestation-campaign`,
    { headers: auth, data: {} }).then((r) => r.json());
  const raw = camp.issued[0].token;

  // ---- a completely clean browser: no token, no user, nothing ----
  const anon = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const anonPage = await anon.newPage();
  const failures: string[] = [];
  anonPage.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) failures.push(`${r.status()} ${r.url()}`);
  });
  await anonPage.goto(`/sign/${raw}`);

  const img = anonPage.locator(".doc-md img");
  await expect(img).toBeVisible();
  // the picture really decoded — a 401 or a stripped src leaves naturalWidth at 0
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth))
    .toBeGreaterThan(0);
  // …through the token-scoped route, never the authenticated one
  await expect(img).toHaveAttribute("src", new RegExp(`/api/sign/${raw}/images/`));
  expect(failures, "the signing page must make no failing API calls").toEqual([]);

  await anon.close();
});
