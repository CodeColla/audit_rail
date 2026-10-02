import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "../index.css";
import { LegalLayout } from "../components/LegalLayout";
import { BUSINESS } from "../lib/business";

function Terms() {
  return (
    <LegalLayout title="Terms of Service">
      <p>Last updated 2 October 2026.</p>

      <h2>1. The service</h2>
      <p>
        Auditrail is a compliance and audit workspace provided by {BUSINESS.legalName} (
        {BUSINESS.entityType}), {BUSINESS.address} ("we", "us"). These terms govern your use of
        the Auditrail web application ("the Service").
      </p>

      <h2>2. Plans and limits</h2>
      <p>
        The Service is offered on a Free plan and a paid Pro plan, each with its own limits on
        active audits and storage, and a custom Enterprise plan agreed separately in writing.
        Current plans and limits are published on our pricing page. We may change plan limits
        or prices for new signups at any time; a change to an existing paid subscription takes
        effect from your next renewal, with notice by email.
      </p>

      <h2>3. Fair use</h2>
      <p>
        Accounts are for one organisation's own compliance and audit work. We may suspend an
        account that is used to resell access, abuse shared infrastructure, or circumvent plan
        limits.
      </p>

      <h2>4. Your data</h2>
      <p>
        You own the content you upload. We store it to provide the Service and do not use it
        to train models or share it with third parties, except where required to operate the
        Service (such as our payment processor) or by law. See our Privacy Policy for detail.
      </p>

      <h2>5. Payments</h2>
      <p>
        Paid plans are billed in advance, monthly or yearly, through Razorpay. See our Refund
        &amp; Cancellation Policy for how cancellations and refunds work.
      </p>

      <h2>6. Termination</h2>
      <p>
        You may cancel at any time from within the Service. We may suspend or terminate an
        account for material breach of these terms, with notice where practicable. On
        termination, we retain your data for a reasonable period to allow export, then delete
        it.
      </p>

      <h2>7. Disclaimer and liability</h2>
      <p>
        The Service is provided "as is". It helps you organise and evidence your compliance
        work; it is not legal, audit or regulatory advice, and using it does not by itself
        guarantee a certification or audit outcome. To the extent permitted by law, our
        liability for any claim relating to the Service is limited to the fees you paid us in
        the 12 months before the claim arose.
      </p>

      <h2>8. Governing law</h2>
      <p>
        These terms are governed by the laws of India, with courts in {BUSINESS.jurisdiction}{" "}
        having exclusive jurisdiction.
      </p>

      <h2>9. Contact</h2>
      <p>
        Questions about these terms: <a href={`mailto:${BUSINESS.email}`}>{BUSINESS.email}</a>.
      </p>
    </LegalLayout>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Terms />
  </React.StrictMode>,
);
