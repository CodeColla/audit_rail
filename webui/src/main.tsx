import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// Self-hosted Inter (SIL OFL, verified in node_modules/@fontsource-variable/inter/LICENSE).
// One variable woff2 covers every weight. Imported here rather than linked from a CDN: Apple's
// SF Pro cannot be licensed for the web at all, so `-apple-system` is the only way to get it
// and it only fires on Apple hardware — Inter is what everyone else sees, and it must not
// depend on a third party being reachable (or on a GDPR question we do not need).
import "@fontsource-variable/inter";
import { AuthProvider } from "./lib/auth";
import App from "./App";
import "./index.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
