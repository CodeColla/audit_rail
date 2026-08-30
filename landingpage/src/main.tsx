import React from "react";
import ReactDOM from "react-dom/client";
// Self-hosted Inter (SIL OFL, same posture as webui/src/main.tsx) — never a Google Fonts
// link. One variable woff2 covers every weight.
import "@fontsource-variable/inter";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
