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
const API_PORT = 5099;
const UI_PORT = 3099;
const E2E_DB = "postgresql+psycopg://audit:audit@localhost:5434/audit_rail_e2e";

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
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        DATABASE_URL: E2E_DB,
        JWT_SECRET: "e2e-secret-not-for-production-use-only-32b",
        SCHEDULER_ENABLED: "false",
        E2E_TEST_HOOKS: "1",
      },
    },
    {
      command: "npm run dev",
      cwd: HERE,
      port: UI_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        VITE_PORT: String(UI_PORT),
        VITE_API_TARGET: `http://127.0.0.1:${API_PORT}`,
      },
    },
  ],
});
