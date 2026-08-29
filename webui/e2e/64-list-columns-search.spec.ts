import { test, expect, Page } from "@playwright/test";

/**
 * Issue #13, Phase 2 — linkage-count columns on Controls/Documents/Evidence list views, and
 * Global Search coverage for Controls and Audits (neither was searchable before).
 *
 * The count columns share one easy-to-get-wrong rule (confirmed with the project owner):
 * "audits linked" counts DISTINCT ASSESSMENTS, not distinct response rows — a document or
 * evidence item cited by two different questions in the SAME audit is linked to that ONE
 * audit, not two. The two "linked to the same audit twice" tests below are the regression
 * guard for that rule specifically; a naive COUNT(*) implementation would fail them.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function token(page: Page) {
  await page.goto("/");
  return page.evaluate(() => localStorage.getItem("ar_token"));
}

async function ownAssessment(page: Page) {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  expect(templates.length).toBeGreaterThan(0);
  const r = await page.request.post("/api/assessments", {
    headers: auth, data: { template_id: templates[0].id, title: `Columns spec ${uniq()}` },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  const aid = (await r.json()).id;
  const rows = await (await page.request.get(`/api/assessments/${aid}/questions`, { headers: auth })).json();
  expect(rows.length, "the seeded template should have 2+ questions").toBeGreaterThan(1);
  // both questions must have an answered response before evidence/documents can attach
  for (const q of rows.slice(0, 2)) {
    const put = await page.request.put(`/api/assessments/${aid}/responses/${q.question_id}`,
      { headers: auth, data: { response_value: "yes" } });
    expect(put.ok(), await put.text()).toBeTruthy();
  }
  return { aid, auth, qA: rows[0], qB: rows[1] };
}

test("controls list shows evidence and document link counts", async ({ page }) => {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const controls = await (await page.request.get("/api/library/controls", { headers: auth })).json();
  expect(controls.length).toBeGreaterThan(0);
  const ctl = controls[0];

  const evTitle = `Count evidence ${uniq()}`;
  const up = await page.request.post("/api/evidence", { headers: auth, multipart: {
    title: evTitle, evidence_type: "REPORT",
    file: { name: "count.txt", mimeType: "text/plain", buffer: Buffer.from("x") } } });
  const evId = (await up.json()).id;
  const linkEv = await page.request.post(`/api/library/controls/${ctl.id}/evidence`,
    { headers: auth, data: { evidence_id: evId } });
  expect(linkEv.ok(), await linkEv.text()).toBeTruthy();

  const people = await (await page.request.get("/api/people", { headers: auth })).json();
  const doc = await page.request.post("/api/documents", {
    headers: auth, data: { title: `Count document ${uniq()}`, owner_person_id: people[0].id } });
  const docId = (await doc.json()).id;
  const linkDoc = await page.request.post(`/api/library/controls/${ctl.id}/documents`,
    { headers: auth, data: { document_id: docId } });
  expect(linkDoc.ok(), await linkDoc.text()).toBeTruthy();

  await page.goto("/controls");
  const row = page.locator("tbody tr", { hasText: ctl.code });
  await expect(row).toBeVisible();
  const cells = await row.locator("td").allTextContents();
  // head: Ref, Control, Lifecycle, Applicability, Mapped, Evidence, Documents
  expect(Number(cells[5])).toBe(ctl.evidence_count + 1);
  expect(Number(cells[6])).toBe(ctl.document_count + 1);
});

test("documents list counts distinct audits, not distinct question links, when linked twice in one audit", async ({ page }) => {
  const { aid, auth, qA, qB } = await ownAssessment(page);
  const people = await (await page.request.get("/api/people", { headers: auth })).json();
  const title = `Distinct-audit document ${uniq()}`;
  const doc = await page.request.post("/api/documents", {
    headers: auth, data: { title, owner_person_id: people[0].id } });
  expect(doc.ok(), await doc.text()).toBeTruthy();
  const docId = (await doc.json()).id;

  for (const q of [qA, qB]) {
    const link = await page.request.post(`/api/assessments/${aid}/responses/${q.question_id}/documents`,
      { headers: auth, data: { document_id: docId } });
    expect(link.ok(), await link.text()).toBeTruthy();
  }

  await page.goto("/documents");
  const row = page.locator("tbody tr", { hasText: title });
  await expect(row).toBeVisible();
  await expect(row).toContainText("1 audits");
  await expect(row).toContainText("0 controls");
});

test("evidence list counts distinct audits, not distinct question links, when linked twice in one audit", async ({ page }) => {
  const { aid, auth, qA, qB } = await ownAssessment(page);
  const title = `Distinct-audit evidence ${uniq()}`;
  const up = await page.request.post("/api/evidence", { headers: auth, multipart: {
    title, evidence_type: "REPORT",
    file: { name: "distinct.txt", mimeType: "text/plain", buffer: Buffer.from("x") } } });
  expect(up.ok(), await up.text()).toBeTruthy();
  const evId = (await up.json()).id;

  for (const q of [qA, qB]) {
    const link = await page.request.post(`/api/assessments/${aid}/responses/${q.question_id}/evidence`,
      { headers: auth, data: { evidence_id: evId } });
    expect(link.ok(), await link.text()).toBeTruthy();
  }

  await page.goto("/evidence");
  const row = page.locator("tbody tr", { hasText: title });
  await expect(row).toBeVisible();
  await expect(row).toContainText("1 audits");
});

test("global search surfaces a matching control", async ({ page }) => {
  const tok = await token(page);
  const auth = { Authorization: `Bearer ${tok}` };
  const controls = await (await page.request.get("/api/library/controls", { headers: auth })).json();
  const ctl = controls[0];

  await page.goto("/");
  const search = page.getByLabel("Search everything");
  await search.fill(ctl.code);
  // scoped to the search widget itself: the sidebar has its own "Controls" nav link with
  // the identical text, which a page-wide getByText would also match
  const panel = search.locator("../..");
  await expect(panel.getByText("Controls", { exact: true })).toBeVisible();
  const hit = panel.getByRole("button", { name: new RegExp(`^${ctl.code}`) });
  await expect(hit).toBeVisible();
  await hit.click();
  await expect(page).toHaveURL(new RegExp(`/controls/view/${ctl.id}`));
});

test("global search surfaces a matching audit", async ({ page }) => {
  const { aid, auth } = await ownAssessment(page);
  const a = await (await page.request.get(`/api/assessments/${aid}`, { headers: auth })).json();

  await page.goto("/");
  const search = page.getByLabel("Search everything");
  await search.fill(a.title);
  const panel = search.locator("../..");
  await expect(panel.getByText("Audits", { exact: true })).toBeVisible();
  const hit = panel.getByRole("button", { name: new RegExp(a.bank_name) });
  await expect(hit).toBeVisible();
  await hit.click();
  await expect(page).toHaveURL(new RegExp(`/audits/${aid}`));
});

test("the audits list has its own search box that filters by title", async ({ page }) => {
  const { auth } = await ownAssessment(page);
  const templates = await (await page.request.get("/api/templates", { headers: auth })).json();
  const uniqueTitle = `Findable audit ${uniq()}`;
  const r = await page.request.post("/api/assessments", {
    headers: auth, data: { template_id: templates[0].id, title: uniqueTitle } });
  expect(r.ok(), await r.text()).toBeTruthy();

  await page.goto("/audits");
  await page.getByLabel("Search audits").fill(uniqueTitle);
  await expect(page.getByText(uniqueTitle)).toBeVisible();
  await expect(page.locator("table tbody tr")).toHaveCount(1);
});
