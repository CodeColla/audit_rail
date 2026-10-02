import { resolve } from "node:path";
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
  // "mpa" turns off Vite's SPA fallback (every unknown path serving index.html) — without it,
  // dev requests for the legal pages below would resolve to the marketing homepage instead.
  // Prod does its own extension-dropping rewrite in nginx (_docker/landing/nginx.conf), so
  // clean URLs still work there; in dev, visit the four pages with their .html extension.
  appType: "mpa",
  build: {
    rollupOptions: {
      // Phase 10 (req/phase10/00-issue-19.md): Razorpay won't activate an account without
      // public Terms/Privacy/Refund/Contact pages. This is a static marketing site with no
      // router (docs/phase9), so each is its own HTML entry rather than a route.
      input: {
        main: resolve(__dirname, "index.html"),
        terms: resolve(__dirname, "terms.html"),
        privacy: resolve(__dirname, "privacy.html"),
        refund: resolve(__dirname, "refund.html"),
        contact: resolve(__dirname, "contact.html"),
      },
    },
  },
});
