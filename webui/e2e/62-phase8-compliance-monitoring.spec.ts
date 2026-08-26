import { test, expect, Page } from "@playwright/test";

/**
 * Phase 8 — asset liveness, alert ingestion, and compliance-config check monitoring
 * (P8-S1/S2/S3b). Backend behaviour is covered by pytest; this proves the three new asset-
 * drawer cards and the control-detail rollup actually render from real API data in a browser,
 * per webui/CLAUDE.md's warning that tsc alone does not verify a UI change.
 */

const uniq = () => Math.random().toString(36).slice(2, 7);

async function apiAsUser(page: Page, method: "post" | "patch" | "get", path: string, data?: unknown) {
  if (!page.url().startsWith("http")) await page.goto("/");
  const token = await page.evaluate(() => localStorage.getItem("ar_token"));
  return page.request[method](`/api${path}`,
    { headers: { Authorization: `Bearer ${token}` }, data: data as any });
}

async function issueLonglivedToken(page: Page, name: string): Promise<string> {
  const install = await apiAsUser(page, "post", "/integrations/tokens/install", { name });
  const { token: installToken } = await install.json();
  const exchange = await page.request.post("/api/integrations/tokens/exchange",
    { data: { install_token: installToken } });
  return (await exchange.json()).token;
}

async function apiAsIntegration(page: Page, path: string, token: string, data: unknown) {
  return page.request.post(`/api${path}`,
    { headers: { Authorization: `Bearer ${token}` }, data: data as any });
}

test("asset drawer shows liveness, a new alert, and a compliance check", async ({ page }) => {
  const tag = `p8-${uniq()}`;
  const created = await apiAsUser(page, "post", "/assets",
    { name: tag, expected_heartbeat_minutes: 60 });
  const { id: assetId } = await created.json();

  const agentToken = await issueLonglivedToken(page, `agent-${tag}`);
  const r1 = await apiAsIntegration(page, "/integrations/heartbeat", agentToken, { asset_id: assetId });
  expect(r1.ok()).toBeTruthy();
  const r2 = await apiAsIntegration(page, "/integrations/alerts", agentToken,
    { asset_id: assetId, title: `Suspicious login ${tag}`, severity: "HIGH" });
  expect(r2.ok()).toBeTruthy();
  const r3 = await apiAsIntegration(page, "/integrations/checks", agentToken, {
    checks: [{ asset_id: assetId, check_key: "mfa_enabled", check_label: "MFA enabled", status: "PASS" }],
  });
  expect(r3.ok()).toBeTruthy();

  await page.goto(`/assets/view/${assetId}`);
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Online", { exact: true })).toBeVisible();

  await expect(drawer.getByText("Alerts needing review")).toBeVisible();
  await expect(drawer.getByText(`Suspicious login ${tag}`)).toBeVisible();

  await expect(drawer.getByText("Compliance checks", { exact: true })).toBeVisible();
  await expect(drawer.getByText("MFA enabled")).toBeVisible();
  await expect(drawer.getByText("Pass", { exact: true })).toBeVisible();

  // review the alert and confirm it drops off the "needs review" card
  await drawer.getByRole("button", { name: "Mark reviewed" }).click();
  await expect(drawer.getByText("Alerts needing review")).toHaveCount(0);
});

test("control detail shows a compliance-check rollup linked back to the asset", async ({ page }) => {
  const tag = `p8ctl-${uniq()}`;
  const created = await apiAsUser(page, "post", "/assets", { name: tag });
  const { id: assetId } = await created.json();

  const domains = await (await apiAsUser(page, "get", "/library/domains")).json();
  const domainId = domains[0].id;
  const controlR = await apiAsUser(page, "post", "/library/controls", {
    domain_id: domainId, code: `P8-${uniq().toUpperCase()}`,
    statement: "MFA is enforced on all admin accounts.",
  });
  const { id: controlId } = await controlR.json();

  const agentToken = await issueLonglivedToken(page, `agent-${tag}`);
  await apiAsIntegration(page, "/integrations/checks", agentToken, {
    checks: [{ asset_id: assetId, control_id: controlId, check_key: "mfa_enabled",
      check_label: "MFA enabled", status: "FAIL" }],
  });

  await page.goto(`/controls/view/${controlId}`);
  await expect(page.getByText("Compliance checks reported")).toBeVisible();
  await expect(page.getByText("1 failing")).toBeVisible();
  await expect(page.getByText(`${tag} · MFA enabled`)).toBeVisible();

  await page.getByText(`${tag} · MFA enabled`).click();
  await expect(page).toHaveURL(new RegExp(`/assets/view/${assetId}`));
});
