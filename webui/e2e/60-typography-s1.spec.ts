import { test, expect } from "@playwright/test";

/**
 * P6-S1 — the typography foundation, measured rather than asserted.
 *
 * Two claims worth pinning, because both are invisible in code review:
 *   1. the font stack actually RESOLVES to the intended family on this machine, and
 *   2. no request leaves for a font CDN.
 *
 * Apple's SF Pro cannot be licensed for web redistribution, so `-apple-system` is the only way
 * to get it and it only fires on Apple hardware. On this Linux box the Inter branch is what
 * renders — which is exactly why the assertion below names Inter and not SF.
 */

test("the app serves its own fonts — nothing goes to a font CDN", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("fonts.googleapis.com") || u.includes("fonts.gstatic.com")) external.push(u);
  });
  await page.goto("/documents");
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  expect(external, "fonts must be self-hosted: no CDN, no GDPR exposure").toEqual([]);
});

test("the resolved typeface is the intended one, not a silent fallback", async ({ page }) => {
  await page.goto("/documents");
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();

  const probe = await page.evaluate(async () => {
    await (document as any).fonts.ready;
    // ENUMERATE the loaded @font-face entries. `document.fonts.check()` is the obvious API and
    // the wrong one: it answers "could this text be rendered with this specification",
    // resolving through fallbacks — so it returns true for a family that was never loaded.
    // Checked against 'Space Grotesk' after its removal and it still said true.
    const families = [...(document as any).fonts].map((f: any) => f.family);
    return { stack: getComputedStyle(document.body).fontFamily, families };
  });

  expect(probe.stack.toLowerCase()).toContain("inter");
  expect(probe.stack.toLowerCase()).toContain("-apple-system");
  expect(probe.families, "Inter must be loaded, not silently falling back")
    .toContain("Inter Variable");
  expect(probe.families.join(" "), "Space Grotesk must be gone entirely")
    .not.toContain("Space Grotesk");
});

test("figures in tables line up — tabular numerals are on by default", async ({ page }) => {
  // Columns of numbers that do not align read as amateur at a glance, and it was previously
  // only applied where someone remembered the .tnum class.
  await page.goto("/risks");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  const variant = await page.evaluate(() => {
    const cell = document.querySelector("tbody td");
    return cell ? getComputedStyle(cell).fontVariantNumeric : "";
  });
  expect(variant).toContain("tabular-nums");
});
