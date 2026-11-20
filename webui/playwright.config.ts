import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// this package is ESM ("type": "module"), so __dirname doesn't exist
const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * Browser suite for audit_rail.
 *
 * Isolation: this brings up its OWN pair of servers — UI 3099 -> API 5099 ->
 * the `audit_rail_e2e` database — so a run never touches the dev stack (UI 3002 /
 * API 5007 / audit_rail) you have open in a browser. Re-seed with:
 *     .venv/bin/python scripts/seed_e2e.py
 *
 * Run it through ../e2e.sh, which supplies the Node 22 (nvm) and the userspace
 * Chromium libs this machine needs — see that script for the why.
 */

const REPO = path.resolve(HERE, "..");
// Ports and the database can be overridden from the environment; the defaults are the
// repo's own local e2e stack and are unchanged when nothing is set.
const API_PORT = Number(process.env.E2E_API_PORT ?? 5099);
const UI_PORT = Number(process.env.E2E_UI_PORT ?? 3099);
const DEFAULT_E2E_DB = "postgresql+psycopg://audit:audit@localhost:5434/audit_rail_e2e";
const E2E_DB = process.env.E2E_DATABASE_URL ?? DEFAULT_E2E_DB;
// Test-only endpoints are enabled only for the ephemeral API this config starts, and only
// against the repo's e2e databases. Any other database runs with hooks off.
const E2E_DB_NAME = new URL(E2E_DB.replace(/^postgresql\+psycopg:/, "postgresql:")).pathname.slice(1);
const HOOKS_ALLOWED_DB = ["audit_rail_e2e", "sr_auditrail_e2e"].includes(E2E_DB_NAME);
// A custom database or port means this run must own its servers: never attach to an
// already-running API that might be pointed somewhere else.
const CUSTOM_STACK = Boolean(process.env.E2E_DATABASE_URL || process.env.E2E_API_PORT || process.env.E2E_UI_PORT);

export default defineConfig({
  testDir: "./e2e",
  // The app is stateful (documents get published, tokens get consumed), so specs
  // run serially by default; opt individual files into parallel when they're pure.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: `http://127.0.0.1:${UI_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    // Logs in once and saves the session; every authed spec reuses it.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/user.json" },
      dependencies: ["setup"],
      testIgnore: /auth\.setup\.ts/,
    },
  ],

  webServer: [
    {
      command: `.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: REPO,
      port: API_PORT,
      reuseExistingServer: !process.env.CI && !CUSTOM_STACK,
      timeout: 60_000,
      env: {
        DATABASE_URL: E2E_DB,
        JWT_SECRET: "e2e-secret-not-for-production-use-only-32b",
        SCHEDULER_ENABLED: "false",
        // Set explicitly in both directions so an inherited shell value cannot enable hooks.
        E2E_TEST_HOOKS: HOOKS_ALLOWED_DB ? "1" : "",
      },
    },
    {
      // Bind IPv4 explicitly: Vite's default "localhost" can resolve to ::1 here, which
      // refuses the 127.0.0.1 baseURL below.
      command: "npm run dev -- --host 127.0.0.1",
      cwd: HERE,
      port: UI_PORT,
      reuseExistingServer: !process.env.CI && !CUSTOM_STACK,
      timeout: 60_000,
      env: {
        VITE_PORT: String(UI_PORT),
        VITE_API_TARGET: `http://127.0.0.1:${API_PORT}`,
      },
    },
  ],
});
