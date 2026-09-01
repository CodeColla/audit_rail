import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 6 — Risks had no edit form at all, only New (create) and Delete. This
 * spec is the direct regression guard against the "field retention" bug and the broader
 * "silently omitted field" failure mode (Tasks' `cadence_months` gap was a live example of
 * exactly this in the same codebase) — every field `RiskPatch` supports gets exercised.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

async function auth(page: Page) {
  const tok = await token(page);
  return { Authorization: `Bearer ${tok}` };
}

async function ownRisk(page: Page, a: Record<string, string>, overrides: Record<string, unknown> = {}) {
  const title = `E2E risk ${uniq()}`;
  const r = await page.request.post("/api/risks", { headers: a, data: { title, ...overrides } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return { id: (await r.json()).id as string, title };
}

/** Opens the risk's drawer then its Edit modal, returning both dialog locators. */
async function openEdit(page: Page, title: string) {
  await page.goto("/risks");
  await page.getByText(title, { exact: true }).click();
  const drawer = page.getByRole("dialog");
  await drawer.getByRole("button", { name: "Edit" }).click();
  const editDialog = page.getByRole("dialog").last();
  return { drawer, editDialog };
}

test("opening Edit pre-populates every field with the risk's current values", async ({ page }) => {
  const a = await auth(page);
  const people = await (await page.request.get("/api/people", { headers: a })).json();
  expect(people.length, "the e2e seed should ship 3+ people").toBeGreaterThan(2);
  const [owner, reporter, reviewer] = people;
  const { title } = await ownRisk(page, a, {
    reference: `REF-${uniq()}`, description: "A description", category: "Access control",
    owner_person_id: owner.id, reported_by_person_id: reporter.id, reviewed_by_person_id: reviewer.id,
    inherent_likelihood: 4, inherent_impact: 5, residual_likelihood: 2, residual_impact: 1,
    treatment: "MITIGATED", note: "A note", next_review_at: "2027-03-01",
  });

  const { editDialog } = await openEdit(page, title);
  const inherentBox = editDialog.locator(".rounded-md.border.border-bd.p-2\\.5").nth(0);
  const residualBox = editDialog.locator(".rounded-md.border.border-bd.p-2\\.5").nth(1);

  await expect(editDialog.getByLabel("Title")).toHaveValue(title);
  await expect(editDialog.getByLabel("Description")).toHaveValue("A description");
  await expect(editDialog.getByLabel("Reference")).not.toHaveValue("");
  await expect(editDialog.getByLabel("Category")).toHaveValue("Access control");
  await expect(editDialog.getByLabel("Owner")).toHaveValue(owner.id);
  await expect(editDialog.getByLabel("Reported by")).toHaveValue(reporter.id);
  await expect(editDialog.getByLabel("Reviewed by")).toHaveValue(reviewer.id);
  await expect(inherentBox.getByLabel("Likelihood")).toHaveValue("4");
  await expect(inherentBox.getByLabel("Impact")).toHaveValue("5");
  await expect(residualBox.getByLabel("Likelihood")).toHaveValue("2");
  await expect(residualBox.getByLabel("Impact")).toHaveValue("1");
  await expect(editDialog.getByLabel("Treatment")).toHaveValue("MITIGATED");
  await expect(editDialog.getByLabel("Status")).toHaveValue("OPEN");
  await expect(editDialog.getByLabel("Next review")).toHaveValue("2027-03-01");
  await expect(editDialog.getByLabel("Note")).toHaveValue("A note");
});

test("editing every field in one pass persists all of them", async ({ page }) => {
  const a = await auth(page);
  const people = await (await page.request.get("/api/people", { headers: a })).json();
  const pick = (i: number) => people[i] ?? people[0];
  const { id, title } = await ownRisk(page, a, { owner_person_id: pick(0).id });

  const { editDialog } = await openEdit(page, title);
  const newTitle = `${title} (edited)`;
  await editDialog.getByLabel("Title").fill(newTitle);
  await editDialog.getByLabel("Description").fill("Edited description");
  await editDialog.getByLabel("Reference").fill(`REF-${uniq()}`);
  await editDialog.getByLabel("Category").selectOption({ label: "Data protection" });
  await editDialog.getByLabel("Owner").selectOption({ value: pick(1).id });
  await editDialog.getByLabel("Reported by").selectOption({ value: pick(2).id });
  await editDialog.getByLabel("Reviewed by").selectOption({ value: pick(0).id });
  const inherentBox = editDialog.locator(".rounded-md.border.border-bd.p-2\\.5").nth(0);
  const residualBox = editDialog.locator(".rounded-md.border.border-bd.p-2\\.5").nth(1);
  await inherentBox.getByLabel("Likelihood").selectOption("3");
  await inherentBox.getByLabel("Impact").selectOption("4");
  await residualBox.getByLabel("Likelihood").selectOption("1");
  await residualBox.getByLabel("Impact").selectOption("2");
  await editDialog.getByLabel("Treatment").selectOption({ label: "Accepted" });
  await editDialog.getByLabel("Status").selectOption({ label: "Closed" });
  await editDialog.getByLabel("Next review").fill("2027-06-15");
  await editDialog.getByLabel("Note").fill("Edited note");
  await editDialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Edit risk" })).toHaveCount(0);

  // verify every field via the API — the direct regression guard against silent field drops
  const detail = await (await page.request.get(`/api/risks/${id}`, { headers: a })).json();
  expect(detail.title).toBe(newTitle);
  expect(detail.description).toBe("Edited description");
  expect(detail.category).toBe("Data protection");
  expect(detail.owner_person_id).toBe(pick(1).id);
  expect(detail.reported_by_person_id).toBe(pick(2).id);
  expect(detail.reviewed_by_person_id).toBe(pick(0).id);
  expect(detail.inherent_likelihood).toBe(3);
  expect(detail.inherent_impact).toBe(4);
  expect(detail.residual_likelihood).toBe(1);
  expect(detail.residual_impact).toBe(2);
  expect(detail.treatment).toBe("ACCEPTED");
  expect(detail.status).toBe("CLOSED");
  expect(detail.next_review_at?.slice(0, 10)).toBe("2027-06-15");
  expect(detail.note).toBe("Edited note");
});

test("setting treatment does not auto-flip status — the project decided that must stay manual", async ({ page }) => {
  const a = await auth(page);
  const { id, title } = await ownRisk(page, a, { status: "OPEN" });

  const { editDialog } = await openEdit(page, title);
  await editDialog.getByLabel("Treatment").selectOption({ label: "Mitigated" });
  // Status is left untouched on purpose — this is the point of the test
  await editDialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Edit risk" })).toHaveCount(0);

  const detail = await (await page.request.get(`/api/risks/${id}`, { headers: a })).json();
  expect(detail.treatment).toBe("MITIGATED");
  expect(detail.status).toBe("OPEN");
});

test("PENDING treatment is selectable and persists from the edit form too, not just create", async ({ page }) => {
  const a = await auth(page);
  const { id, title } = await ownRisk(page, a);

  const { editDialog } = await openEdit(page, title);
  await editDialog.getByLabel("Treatment").selectOption({ label: "Pending" });
  await editDialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Edit risk" })).toHaveCount(0);

  const detail = await (await page.request.get(`/api/risks/${id}`, { headers: a })).json();
  expect(detail.treatment).toBe("PENDING");
});
