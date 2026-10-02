import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "../index.css";
import { LegalLayout } from "../components/LegalLayout";
import { BUSINESS } from "../lib/business";

function Contact() {
  return (
    <LegalLayout title="Contact Us">
      <p>
        {BUSINESS.legalName} ({BUSINESS.entityType})
      </p>
      <p>{BUSINESS.address}</p>
      <p>
        Email: <a href={`mailto:${BUSINESS.email}`}>{BUSINESS.email}</a>
      </p>
      <p>Phone: {BUSINESS.phone}</p>
    </LegalLayout>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Contact />
  </React.StrictMode>,
);
