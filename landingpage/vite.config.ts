import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standalone from webui's dev server (3002) and the Playwright e2e stack's UI (3099) — no
// /api proxy here at all: this is a static marketing site with no backend dependency (the
// CTA points at an absolute, externally-reachable webui URL baked in at build time — see
// src/lib/env.ts).
const port = Number(process.env.VITE_PORT ?? 3003);

export default defineConfig({
  plugins: [react()],
  server: { port },
});
