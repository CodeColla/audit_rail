import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "../index.css";
import { LegalLayout } from "../components/LegalLayout";
import { BUSINESS } from "../lib/business";

function Privacy() {
  return (
    <LegalLayout title="Privacy Policy">
      <p>Last updated 2 October 2026.</p>

      <h2>What we collect</h2>
      <p>
        When you sign up we collect your name, work email and organisation name. As you use
        the Service, we store what you put into it — audits, controls, evidence files,
        policies, documents, and the people and task records you create.
      </p>

      <h2>Where it's stored</h2>
      <p>
        Account and audit data lives in our database; uploaded files live in our file storage.
        Both are hosted on infrastructure we control. We do not sell your data or share it
        with advertisers.
      </p>

      <h2>Payments</h2>
      <p>
        If you upgrade to a paid plan, payment is handled by Razorpay. We never see or store
        your card, UPI or bank details — Razorpay shares back only what we need to run your
        subscription (status, plan, renewal date) and to generate an invoice.
      </p>

      <h2>Who can see your data</h2>
      <p>
        Only people you invite into your organisation, under the role you give them. Guest
        auditors you invite to a specific audit see only that audit, in read-only form. Our
        own staff access customer data only to provide support, when you ask us to, or to keep
        the Service secure.
      </p>

      <h2>How long we keep it</h2>
      <p>
        We keep your data for as long as your account is active, plus a reasonable period
        after cancellation so you can export it, after which we delete it.
      </p>

      <h2>Your choices</h2>
      <p>
        You can export or delete your own data from within the Service, or ask us to do so at{" "}
        <a href={`mailto:${BUSINESS.email}`}>{BUSINESS.email}</a>.
      </p>

      <h2>Contact</h2>
      <p>
        {BUSINESS.legalName}, {BUSINESS.address} —{" "}
        <a href={`mailto:${BUSINESS.email}`}>{BUSINESS.email}</a>.
      </p>
    </LegalLayout>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Privacy />
  </React.StrictMode>,
);
