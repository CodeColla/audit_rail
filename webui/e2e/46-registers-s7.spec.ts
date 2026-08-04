import { test, expect, Page } from "@playwright/test";

/**
 * P4-S7 — the five registers as first-class modules, in the browser.
 *
 * tests/test_registers_s7.py already proves the API: type-aware asset validation, the
 * evidence join tables, the append-only incident timeline, agreement file upload. What
 * only a browser can prove is that the *fields reach the wire at all* — S7 exists almost
 * entirely because columns like `assets.vendor_third_party_id`, `data_items.data_type` and
 * `third_party_agreements.file_id` had shipped with no UI able to populate them. A green
 * API test says nothing about that gap; a form that never renders the input does.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

/**
 * Seeded titles contain "[demo] …", and `[demo]` is a regex *character class*. Anything
 * built with `new RegExp(title)` silently matches nothing.
 */
const re = (s: string) => new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

/**
 * Selects must be located by ROLE, not by label. `getByLabel` matches a wrapping label's
 * textContent, and for a <select> that includes every option's text — so exact:true never
 * matches, and non-exact "Type" also matches "Sub-type". The accessible name is clean.
 */
const combo = (scope: Page | ReturnType<Page["getByRole"]>, name: string) =>
  scope.getByRole("combobox", { name, exact: true });

/** Read straight from the API to assert what the form actually persisted. */
async function apiGet(page: Page, path: string): Promise<any> {
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  const r = await page.request.get(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(r.ok(), `${path} -> ${r.status()} ${await r.text()}`).toBeTruthy();
  return r.json();
}

// ─────────────────────────────────────────────────────────────── assets
test.describe("assets are type-aware", () => {
  test("the physical fields swap for virtual ones, and only the shown set is sent", async ({ page }) => {
    const name = `E2E asset ${uniq()}`;
    await page.goto("/assets");
    await page.getByRole("button", { name: /New asset/ }).click();

    // VIRTUAL is the default: hostname/ip/cloud, no serial number
    await page.getByLabel("Name *").fill(name);
    await expect(page.getByLabel("Hostname")).toBeVisible();
    await expect(page.getByLabel("Serial number")).toHaveCount(0);
    await page.getByLabel("Hostname").fill("host-that-is-discarded");

    // switching to PHYSICAL must swap the whole block
    await combo(page, "Type").selectOption("PHYSICAL");
    await expect(page.getByLabel("Hostname")).toHaveCount(0);
    await page.getByLabel("Manufacturer").fill("Dell");
    await page.getByLabel("Model").fill("PowerEdge R740");
    await page.getByLabel("Serial number").fill("SN-99312");
    await page.getByRole("button", { name: "Create asset" }).click();

    await expect(page.getByRole("dialog")).toHaveCount(0);
    const rows = await apiGet(page, "/assets");
    const made = rows.find((a: any) => a.name === name);
    expect(made, "the asset should exist").toBeTruthy();

    const det = await apiGet(page, `/assets/${made.id}`);
    expect(det.asset_type).toBe("PHYSICAL");
    expect(det.serial_number).toBe("SN-99312");
    expect(det.manufacturer).toBe("Dell");
    // the virtual value typed before the swap must NOT have been posted
    expect(det.hostname).toBeNull();
  });

  test("an asset can name the vendor that supplies it", async ({ page }) => {
    const vendors = await (async () => { await page.goto("/assets"); return apiGet(page, "/third-parties"); })();
    test.skip(vendors.length === 0, "needs a seeded vendor");
    const name = `E2E vendored ${uniq()}`;

    await page.getByRole("button", { name: /New asset/ }).click();
    await page.getByLabel("Name *").fill(name);
    await combo(page, "Vendor").selectOption(vendors[0].id);
    await page.getByRole("button", { name: "Create asset" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // and the drawer links through to that vendor
    const rows = await apiGet(page, "/assets");
    const made = rows.find((a: any) => a.name === name);
    await page.goto(`/assets/view/${made.id}`);
    await expect(page.getByRole("link", { name: vendors[0].name })).toBeVisible();
  });

  test("a physical asset carries a photo; a virtual one is never asked for one", async ({ page }) => {
    const name = `E2E photo ${uniq()}`;
    await page.goto("/assets");
    await page.getByRole("button", { name: /New asset/ }).click();
    await page.getByLabel("Name *").fill(name);
    await combo(page, "Type").selectOption("PHYSICAL");
    await page.getByRole("button", { name: "Create asset" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: name }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("Add a photo of this unit")).toBeVisible();

    // a 1×1 PNG is enough to prove the round trip
    await drawer.locator('input[type="file"]').setInputFiles({
      name: "rack.png", mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
        "base64"),
    });
    // the <img> only appears once the blob has been fetched back through the API
    await expect(drawer.locator("img[alt='rack.png']")).toBeVisible();

    await drawer.getByRole("button", { name: "Remove" }).click();
    await expect(drawer.getByText("Add a photo of this unit")).toBeVisible();
  });

  test("a virtual asset has no photo card at all", async ({ page }) => {
    const name = `E2E nophoto ${uniq()}`;
    await page.goto("/assets");
    await page.getByRole("button", { name: /New asset/ }).click();
    await page.getByLabel("Name *").fill(name);           // VIRTUAL is the default
    await page.getByRole("button", { name: "Create asset" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: name }).click();
    // `exact` matters here: getByText(string) is case-INSENSITIVE substring matching, and
    // this asset is called "E2E no{photo} …", so a loose match hits the drawer's own <h3>
    // title rather than a Photo card. The card's label is exactly "Photo" (Registers.tsx's
    // `<div className="eyebrow">Photo</div>`), so anchoring on that is what the test meant.
    // Without this the assertion only passed by racing the title's render — it went red the
    // moment anything shifted app timing, while the feature itself was always correct.
    await expect(page.getByRole("dialog").getByText("Photo", { exact: true })).toHaveCount(0);
  });
});

test.describe("risks", () => {
  test("a risk records who reported and who reviewed it, and can carry evidence", async ({ page }) => {
    await page.goto("/risks");
    const people = await apiGet(page, "/people");
    const vault = await apiGet(page, "/evidence");
    test.skip(people.length === 0 || vault.length === 0, "needs seeded people and evidence");

    const title = `E2E risk ${uniq()}`;
    await page.getByRole("button", { name: /New risk/ }).click();
    await page.getByLabel("Title *").fill(title);
    await combo(page, "Reported by").selectOption(people[0].id);
    await combo(page, "Reviewed by").selectOption(people[0].id);
    await page.getByRole("button", { name: "Create risk" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const rows = await apiGet(page, "/risks");
    const made = rows.find((r: any) => r.title === title);
    await page.goto(`/risks/view/${made.id}`);
    await expect(page.getByText("Reported by")).toBeVisible();
    await expect(page.getByText("Reviewed by")).toBeVisible();

    // attach evidence from the vault — the picker only fetches once opened
    const riskDrawer = page.getByRole("dialog");
    await riskDrawer.getByRole("button", { name: /Attach/ }).click();
    await riskDrawer.getByRole("button", { name: re(vault[0].title) }).click();
    await expect(riskDrawer.getByText(/Evidence · 1/)).toBeVisible();

    const det = await apiGet(page, `/risks/${made.id}`);
    expect(det.evidence.map((e: any) => e.id)).toEqual([vault[0].id]);
  });
});

// ─────────────────────────────────────────────────────────────── data inventory
test.describe("data inventory", () => {
  test("a data item records where it lives", async ({ page }) => {
    const name = `E2E data ${uniq()}`;
    await page.goto("/data");
    await page.getByRole("button", { name: /New data item/ }).click();
    await page.getByLabel("Name *").fill(name);
    await combo(page, "Where it lives").selectOption("Database");
    await page.getByRole("button", { name: "Create data item" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    const row = page.locator("tbody tr", { hasText: name });
    await expect(row).toContainText("Database");
  });
});

// ─────────────────────────────────────────────────────────────── third parties
test.describe("third parties", () => {
  test("the signed contract can finally be attached to an agreement", async ({ page }) => {
    const name = `E2E vendor ${uniq()}`;
    await page.goto("/third-parties");
    await page.getByRole("button", { name: /New third party/ }).click();
    await page.getByLabel("Name *").fill(name);
    await page.getByRole("button", { name: "Create vendor" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: name }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByPlaceholder("Contract ref").fill("MSA-2026-01");
    await drawer.getByRole("button", { name: "Add agreement" }).click();
    await expect(drawer.getByText("MSA-2026-01")).toBeVisible();

    // the "contract" control is a hidden <input type=file> behind a label
    await drawer.getByText("contract", { exact: true }).click({ trial: true });
    await drawer.locator('input[type="file"]').setInputFiles({
      name: "dpa-signed.pdf", mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n% e2e contract\n"),
    });
    await expect(drawer.getByRole("button", { name: /dpa-signed\.pdf/ })).toBeVisible();

    const tps = await apiGet(page, "/third-parties");
    const det = await apiGet(page, `/third-parties/${tps.find((t: any) => t.name === name).id}`);
    expect(det.agreements[0].file_name).toBe("dpa-signed.pdf");
    expect(det.agreements[0].reference).toBe("MSA-2026-01");
  });

  test("an assessment points at the report that backs it", async ({ page }) => {
    await page.goto("/third-parties");
    const vault = await apiGet(page, "/evidence");
    test.skip(vault.length === 0, "needs seeded evidence");

    const name = `E2E assessed ${uniq()}`;
    await page.getByRole("button", { name: /New third party/ }).click();
    await page.getByLabel("Name *").fill(name);
    await page.getByRole("button", { name: "Create vendor" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: name }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("button", { name: "Add assessment" }).click();
    await expect(drawer.getByText(/expires/).first()).toBeVisible();

    await drawer.getByRole("button", { name: /evidence/i }).click();
    await combo(drawer, "Pick evidence").selectOption(vault[0].id);
    await expect(drawer.getByRole("link", { name: re(vault[0].title) })).toBeVisible();
  });
});

// ─────────────────────────────────────────────────────────────── incidents
test.describe("incidents", () => {
  test("the timeline is append-only and RCA fields round-trip", async ({ page }) => {
    const title = `E2E incident ${uniq()}`;
    await page.goto("/incidents");
    await page.getByRole("button", { name: /New incident/ }).click();
    await page.getByLabel("Title *").fill(title);
    await page.getByRole("button", { name: "Create incident" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.locator("tbody tr", { hasText: title }).click();
    const drawer = page.getByRole("dialog");

    // two timeline entries, in the order they were logged
    await combo(drawer, "Timeline entry type").selectOption("DETECTED");
    await drawer.getByPlaceholder("What happened, and when?").fill("Monitoring alerted at 02:14.");
    await drawer.getByRole("button", { name: "Add to timeline" }).click();
    await expect(drawer.getByText("Monitoring alerted at 02:14.")).toBeVisible();

    await combo(drawer, "Timeline entry type").selectOption("CONTAINMENT");
    await drawer.getByPlaceholder("What happened, and when?").fill("Failed over to the DR site.");
    await drawer.getByRole("button", { name: "Add to timeline" }).click();
    await expect(drawer.getByText(/Timeline · 2/)).toBeVisible();
    await expect(drawer.getByText("Entries can't be edited or deleted")).toBeVisible();

    // corrective action and resolution save alongside the existing RCA fields
    await drawer.getByLabel("Root cause").fill("A stale DNS record.");
    await drawer.getByLabel("Corrective action").fill("Pinned the record and added a check.");
    await drawer.getByLabel("Resolution").fill("Service restored 02:51.");
    await drawer.getByRole("button", { name: "Save RCA" }).click();

    const rows = await apiGet(page, "/incidents");
    const made = rows.find((i: any) => i.title === title);
    await expect
      .poll(async () => (await apiGet(page, `/incidents/${made.id}`)).corrective_action)
      .toBe("Pinned the record and added a check.");
    const det = await apiGet(page, `/incidents/${made.id}`);
    expect(det.resolution).toBe("Service restored 02:51.");
    expect(det.events.map((e: any) => e.event_type)).toEqual(["DETECTED", "CONTAINMENT"]);
  });
});
