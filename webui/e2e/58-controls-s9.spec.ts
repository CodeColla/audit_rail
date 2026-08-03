import { test, expect } from "@playwright/test";

/**
 * P5-S9 Slice A — a new organisation can use Controls on day one.
 *
 * Sumit, after creating a second org: "when i create a new org then the controls domains are
 * there, but no checklist is assigned." The domains were seeded; the controls were not.
 * `scripts/build_control_library.py` was a one-time bootstrap that only ever ran against the
 * first install, so open signup produced 16 labelled but empty shelves.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

/**
 * Sign up a brand-new organisation and return its page.
 *
 * Used by every spec here, not just the "what does a new org get" one: these specs MAP
 * controls to clauses, which is a persistent change. Run against the shared seeded org they
 * pass once and then fail forever, because the clause is already mapped and the picker
 * filters it out. A fresh org per run is both realistic and repeatable.
 */
async function freshOrg(browser: import("@playwright/test").Browser) {
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  const tag = uniq();
  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Library Owner");
  await page.getByLabel("Work email").fill(`s9-${tag}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(`S9 Co ${tag}`);
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
  return { ctx, page };
}

test("a brand-new organisation opens Controls to a real library", async ({ browser }) => {
  // A CLEAN context: this is about what a NEW org gets, so the shared seeded session — which
  // has had a control library since the very first install — would prove nothing.
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  const tag = uniq();

  await page.goto("/signup");
  await page.getByLabel("Your name").fill("Library Owner");
  await page.getByLabel("Work email").fill(`lib-${tag}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("Passw0rdOne");
  await page.getByLabel("Organisation name").fill(`Library Co ${tag}`);
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Controls", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Controls" })).toBeVisible();

  // the actual assertion: there are controls, not an empty table
  await expect(page.locator("tbody tr").first()).toBeVisible();
  expect(await page.locator("tbody tr").count()).toBeGreaterThan(10);
  await expect(page.getByText("AM 3.a")).toBeVisible();

  // and Cloud Security is applicable — the build script marks it dormant for KIAM, an on-prem
  // vendor, which must never have become everyone's default
  await page.getByRole("button", { name: /^CS/ }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect(page.getByText(/not applicable/i)).toHaveCount(0);

  await ctx.close();
});

/**
 * Slice B — certification frameworks over the ONE control library.
 *
 * Sumit's question was whether master controls should be organised per certification. This is
 * the answer made visible: one control, mapped to ISO and SOC 2, moves BOTH coverage bars.
 * A per-certification control set would need the same control (and its evidence) twice.
 */
test("one control mapped to two certifications moves both coverage bars", async ({ browser }) => {
  const { ctx, page } = await freshOrg(browser);

  await page.goto("/controls");
  await page.getByRole("link", { name: "Certifications" }).click();
  await expect(page.getByRole("heading", { name: "Frameworks" })).toBeVisible();
  await expect(page.getByText("ISO/IEC 27001:2022 Annex A")).toBeVisible();
  await expect(page.getByText("SOC 2 Trust Services Criteria")).toBeVisible();
  await expect(page.getByText(/^0 of \d+ clauses/)).toHaveCount(3);   // nothing mapped yet

  await page.goto("/controls");
  await page.getByText("AM 3.a").click();
  await expect(page.getByRole("heading", { name: /Strong authentication/ })).toBeVisible();

  for (const [framework, ref] of [["ISO/IEC 27001:2022 Annex A", "A.8.5"],
                                  ["SOC 2 Trust Services Criteria", "CC6.1"]] as const) {
    await page.getByRole("button", { name: /Map a clause/ }).click();
    await page.getByLabel("Framework").selectOption({ label: framework });
    await page.getByRole("button", { name: new RegExp(`^${ref.replace(".", "\\.")}`) }).click();
    await expect(page.getByText(ref, { exact: false }).first()).toBeVisible();
  }

  // Both certifications now count that one control — the whole point of one library with many
  // tags. Asserted as a count: two frameworks read "1 of N", the untouched third still "0 of N".
  await page.goto("/frameworks");
  await expect(page.getByText(/^1 of \d+ clauses/)).toHaveCount(2);
  await expect(page.getByText(/^0 of \d+ clauses/)).toHaveCount(1);
  await ctx.close();
});

test("readiness separates 'not mapped' from 'mapped but unproven'", async ({ browser }) => {
  // A clause with a control but no evidence must NOT read as covered — that is the difference
  // between a compliance dashboard and a comfortable lie.
  const { ctx, page } = await freshOrg(browser);

  await page.goto("/controls");
  await page.getByText("AM 3.a").click();
  await page.getByRole("button", { name: /Map a clause/ }).click();
  await page.getByLabel("Framework").selectOption({ label: "ISO/IEC 27001:2022 Annex A" });
  await page.getByRole("button", { name: /^A\.8\.5/ }).click();
  await expect(page.getByText("A.8.5").first()).toBeVisible();

  await page.goto("/frameworks");
  await page.getByRole("link", { name: "ISO/IEC 27001:2022 Annex A" }).click();

  // mapped, but nothing proves it
  await page.getByRole("button", { name: /^No proof 1$/ }).click();
  await expect(page.getByText("A.8.5")).toBeVisible();
  await expect(page.getByText("No current proof")).toBeVisible();

  // and it is NOT counted as covered
  await page.getByRole("button", { name: /^Covered 0$/ }).click();
  await expect(page.getByText("Nothing in this state.")).toBeVisible();
  await ctx.close();
});
