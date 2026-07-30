import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the SPA runs on 3002 and proxies /api to the FastAPI backend (5007),
// so there are no CORS hoops in local dev.
//
// Both are overridable via env so the Playwright suite can bring up an isolated
// pair (UI 3099 -> API 5099 -> audit_rail_e2e database) without touching the dev
// ports you have open in a browser. See playwright.config.ts.
const uiPort = Number(process.env.VITE_PORT ?? 3002);
const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:5007";

export default defineConfig({
  plugins: [react()],
  server: {
    port: uiPort,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
});
