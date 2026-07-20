import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the SPA runs on 3002 and proxies /api to the FastAPI backend (5007),
// so there are no CORS hoops in local dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3002,
    proxy: {
      "/api": { target: "http://127.0.0.1:5007", changeOrigin: true },
    },
  },
});
