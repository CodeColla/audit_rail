import { test, expect, APIRequestContext, Browser } from "@playwright/test";

/**
 * P4-S8 — the auditor guest portal, in a real browser, for the first time.
 *
 * No spec had ever run as a guest: `30-audit-journey.spec.ts` exercises the auditor *view*
 * while logged in as a member, which is exactly the setup that hid this sprint's headline
 * bug. `GET /evidence/{id}/file` is gated on `require("evidence","view")`, which rejects
 * every non-member with 403 "Member access required" — so the invited bank auditor could
 * read the titles of the proof behind every answer and open none of it. As a member, the
 * same click worked, and the suite stayed green.
 *
 * These tests therefore mint a genuine guest token and drive a context that has never
 * seen the member session.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

/** An authenticated API context using the saved member session. */
async function memberApi(browser: Browser): Promise<{ api: APIRequestContext; token: string }> {
  const ctx = await browser.newContext();          // storageState comes from the project
  const page = await ctx.newPage();
  await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  await page.close();
  return { api: ctx.request, token: token! };
}

type Fixture = { aid: string; guestToken: string; evTitle: string; qid: string };

/**
 * Build a whole assessment as a member: template -> assessment -> answered question ->
 * uploaded evidence attached to that answer -> a guest invitation.
 *
 * `seed_e2e.py` ships zero assessments on purpose, and the four artifacts it does create
 * (via seed_demo.py) are all medium='LINK' with no file behind them — so a spec that needs
 * downloadable bytes has to upload its own.
 */
async function buildAudit(browser: Browser): Promise<Fixture> {
  const { api, token } = await memberApi(browser);
  const h = { Authorization: `Bearer ${token}` };
  const tag = uniq();

  const templates = await (await api.get("/api/templates", { headers: h })).json();
  const tpl = (templates.items ?? templates)[0];
  expect(tpl, "the e2e seed should ship a template").toBeTruthy();

  const aRes = await api.post("/api/assessments", {
    headers: h, data: { template_id: tpl.id, title: `Guest audit ${tag}` } });
  expect(aRes.ok(), await aRes.text()).toBeTruthy();
  const aid = (await aRes.json()).id;

  const grid = await (await api.get(`/api/assessments/${aid}/questions`, { headers: h })).json();
  const qid = grid[0].question_id;
  await api.put(`/api/assessments/${aid}/responses/${qid}`, {
    headers: h, data: { response_value: "yes", comment: "Answered for the guest spec." } });

  const evTitle = `Guest proof ${tag}`;
  const up = await api.post("/api/evidence", {
    headers: h,
    multipart: {
      title: evTitle, evidence_type: "report",
      file: { name: "guest-proof.pdf", mimeType: "application/pdf",
              buffer: Buffer.from("%PDF-1.4\nguest proof\n") },
    },
  });
  expect(up.ok(), await up.text()).toBeTruthy();
  const eid = (await up.json()).id;
  const linked = await api.post(`/api/assessments/${aid}/responses/${qid}/evidence`, {
    headers: h, data: { evidence_id: eid } });
  expect(linked.ok(), await linked.text()).toBeTruthy();

  // the invitation returns the token in the body — nothing is emailed
  const inv = await api.post(`/api/assessments/${aid}/guests`, {
    headers: h, data: { email: `auditor-${tag}@bank.example`, full_name: "A. Auditor",
                        firm: "PwC", expires_at: "2027-12-31" } });
  expect(inv.ok(), await inv.text()).toBeTruthy();
  return { aid, guestToken: (await inv.json()).access_token, evTitle, qid };
}

test("an auditor guest can open the evidence behind an answer", async ({ browser }) => {
  const f = await buildAudit(browser);

  // a context that has never held the member session
  const guestCtx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await guestCtx.newPage();
  await page.goto(`/auditor?token=${f.guestToken}`);

  await expect(page.getByText(/You are reviewing/)).toBeVisible({ timeout: 15_000 });
  await page.locator("tbody tr").first().click();

  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText(f.evTitle)).toBeVisible();

  const download = page.waitForEvent("download");
  await drawer.getByRole("button", { name: f.evTitle }).click();
  // Before P4-S8 this click resolved to a 403 that nothing caught, so nothing happened at
  // all — no download, no error, no clue.
  expect((await download).suggestedFilename()).toBe("guest-proof.pdf");
  await expect(drawer.getByText(/Could not open/)).toHaveCount(0);

  await guestCtx.close();
});

test("the guest's token cannot reach the member evidence vault", async ({ browser }) => {
  const f = await buildAudit(browser);
  const guestCtx = await browser.newContext({ storageState: { cookies: [], origins: [] } });

  for (const path of ["/api/evidence", "/api/library/controls", "/api/risks"]) {
    const r = await guestCtx.request.get(path, {
      headers: { Authorization: `Bearer ${f.guestToken}` } });
    expect(r.status(), `${path} must stay member-only`).toBe(403);
  }
  await guestCtx.close();
});

test("a guest cannot pull an artifact that is not attached to their assessment", async ({ browser }) => {
  const f = await buildAudit(browser);
  const { api, token } = await memberApi(browser);
  const h = { Authorization: `Bearer ${token}` };

  // an artifact in the vendor's vault, attached to nothing
  const up = await api.post("/api/evidence", {
    headers: h,
    multipart: { title: `Private ${uniq()}`, evidence_type: "report",
                 file: { name: "private.pdf", mimeType: "application/pdf",
                         buffer: Buffer.from("%PDF-1.4\nprivate\n") } },
  });
  const loose = (await up.json()).id;

  const guestCtx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const r = await guestCtx.request.get(`/api/assessments/${f.aid}/evidence/${loose}/file`, {
    headers: { Authorization: `Bearer ${f.guestToken}` } });
  // 404, not 403 — a 403 would confirm the id exists and turn this into an enumeration oracle
  expect(r.status()).toBe(404);
  await guestCtx.close();
});
