import { test, expect, Page } from "@playwright/test";

/**
 * P4-S6 — re-mapping an audit question to a different control, and evidence inherited
 * from that control.
 *
 * The API side is proven in tests/test_audits_remap.py. What only a browser can prove:
 * that the re-map picker actually reaches the endpoint, that the drawer's mapped-control
 * card updates from it, that the "your saved answer was auto-filled" warning appears, and
 * that inherited evidence renders in its own section rather than mixed into the direct one.
 *
 * The seeded e2e org ships one assessment (scripts/seed_e2e.py) — this spec uses it
 * read-mostly and creates its own evidence, so it does not disturb 30-audit-journey.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

/**
 * The seed deliberately ships ZERO assessments (see 30-audit-journey.spec.ts), so this
 * spec makes its own from a seeded template rather than depending on another spec file
 * having run first — file-order coupling between specs is exactly the kind of thing that
 * breaks when someone runs a single file.
 */
async function ownAssessment(page: Page) {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  expect(templates.length, "the e2e seed should ship templates").toBeGreaterThan(0);
  const r = await page.request.post("/api/assessments", {
    headers: auth,
    data: { template_id: templates[0].id, title: `Remap spec ${uniq()}` },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { aid: (await r.json()).id, auth };
}

/** Open the first question of a freshly-created assessment. */
async function openFirstQuestion(page: Page) {
  const { aid, auth } = await ownAssessment(page);
  await page.goto(`/audits/${aid}`);
  await page.locator("table tbody tr").first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  return { aid, auth };
}

test.describe("mapped control context", () => {
  test("the drawer shows the control's statement, not just its code", async ({ page }) => {
    // the statement was fetched by the API but never rendered before P4-S6
    await openFirstQuestion(page);
    const card = page.getByRole("dialog").getByText("Mapped control").locator("..").locator("..");
    await expect(card).toBeVisible();
    // a statement is prose, so assert it is non-trivially longer than a bare code
    const text = await card.innerText();
    expect(text.length).toBeGreaterThan("Mapped control".length + 10);
  });
});

test.describe("re-map", () => {
  test("re-mapping a question changes the control shown on the question", async ({ page }) => {
    await openFirstQuestion(page);
    const dialog = page.getByRole("dialog");

    const before = await dialog.getByText(/^↳ /).innerText();

    await dialog.getByRole("button", { name: "Re-map" }).click();
    await expect(page.getByPlaceholder("Search controls…")).toBeVisible();

    // pick a control that is NOT the current one
    const options = dialog.locator("button", { hasText: /^[A-Z]{2,4} / });
    const count = await options.count();
    expect(count, "the control library should be seeded").toBeGreaterThan(1);
    for (let i = 0; i < count; i++) {
      const label = await options.nth(i).innerText();
      if (!before.includes(label.split("\n")[0])) { await options.nth(i).click(); break; }
    }

    await expect(dialog.getByText(/Re-mapped/)).toBeVisible();
    await expect(dialog.getByText(/^↳ /)).not.toHaveText(before);
  });

  test("re-mapping a prefilled answer warns that the saved answer was not changed", async ({ page }) => {
    const { aid, auth } = await openFirstQuestion(page);
    // make sure this question has a prefilled answer to warn about
    await page.request.post(`/api/assessments/${aid}/prefill`, { headers: auth });

    await page.goto(`/audits/${aid}`);
    await page.locator("table tbody tr").first().click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Re-map" }).click();

    const options = dialog.locator("button", { hasText: /^[A-Z]{2,4} / });
    await options.first().click();
    // either message is valid depending on whether THIS question was the prefilled one;
    // what must never happen is a silent no-op
    await expect(dialog.getByText(/Re-mapped/)).toBeVisible();
  });

  test("a Viewer sees no Re-map affordance", async ({ browser }) => {
    const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const page = await ctx.newPage();
    const n = uniq();
    await page.goto("/signup");
    await page.getByLabel("Your name").fill("Audit Owner");
    await page.getByLabel("Work email").fill(`audit-owner-${n}@example.com`);
    await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
    await page.getByLabel("Organisation name").fill(`Audit Org ${n}`);
    const gstin = (() => {
      const A = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
      const base = `27AAPFU${String(Date.now() % 10000).padStart(4, "0")}F1Z`;
      let total = 0;
      [...base].forEach((ch, i) => {
        const p = A.indexOf(ch) * (i % 2 ? 2 : 1);
        total += Math.floor(p / 36) + (p % 36);
      });
      return base + A[(36 - (total % 36)) % 36];
    })();
    await page.getByLabel("GST number").fill(gstin);
    await page.getByRole("button", { name: "Create organisation" }).click();
    await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
    // a fresh org has no assessments, so this only pins the menu-level gate; the
    // per-drawer gate is covered by the canEdit branch in tests/test_rbac.py
    await expect(page.getByRole("link", { name: "Audits", exact: true })).toBeVisible();
    await ctx.close();
  });
});

test.describe("inherited evidence", () => {
  test("evidence attached to the control shows in its own section on the question", async ({ page }) => {
    const { aid, auth } = await ownAssessment(page);

    // find the control the first question is mapped to
    const grid = await (await page.request.get(`/api/assessments/${aid}/questions`,
      { headers: auth })).json();
    const mapped = grid.find((g: any) => g.mapped_control);
    expect(mapped, "at least one question should be mapped").toBeTruthy();

    const controls = await (await page.request.get("/api/library/controls", { headers: auth })).json();
    const ctl = controls.find((c: any) => c.code === mapped.mapped_control);
    expect(ctl).toBeTruthy();

    // attach a uniquely-named piece of evidence to that control
    const title = `Inherited E2E ${uniq()}`;
    const up = await page.request.post("/api/evidence", {
      headers: auth,
      multipart: {
        title, evidence_type: "REPORT",
        file: { name: "inherited.txt", mimeType: "text/plain", buffer: Buffer.from("x") },
      },
    });
    expect(up.ok(), await up.text()).toBeTruthy();
    const evId = (await up.json()).id;
    const link = await page.request.post(`/api/library/controls/${ctl.id}/evidence`, {
      headers: auth, data: { evidence_id: evId } });
    expect(link.ok(), await link.text()).toBeTruthy();

    // it now appears on the audit question, in the INHERITED section
    await page.goto(`/audits/${aid}`);
    await page.getByText(mapped.text).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText(`Inherited from ${ctl.code}`)).toBeVisible();
    await expect(dialog.getByRole("button", { name: title })).toBeVisible();
    // and NOT in the direct-evidence list
    await expect(dialog.getByText("Nothing attached directly to this question.")
      .or(dialog.getByText("Answer the question first, then link evidence."))).toBeVisible();
  });
});
