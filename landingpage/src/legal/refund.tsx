import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "../index.css";
import { LegalLayout } from "../components/LegalLayout";
import { BUSINESS } from "../lib/business";

// The policy text below is locked (req/phase10/00-issue-19.md §7) — change it there first,
// then here, not the other way round.
function Refund() {
  return (
    <LegalLayout title="Refund & Cancellation Policy">
      <p>Last updated 2 October 2026.</p>

      <h2>Cancelling</h2>
      <p>
        You can cancel your Pro subscription at any time from Billing inside the Service.
        Cancelling stops future renewals; your access continues until the end of the period
        you've already paid for.
      </p>

      <h2>Refunds</h2>
      <ul>
        <li>
          <strong>Monthly payments</strong> are non-refundable.
        </li>
        <li>
          <strong>Yearly payments</strong> get a full refund if you request it within 7 days
          of the first payment on that subscription.
        </li>
      </ul>
      <p>
        Approved refunds are returned to your original payment method within 5–7 working
        days, through Razorpay.
      </p>

      <h2>How to request a refund</h2>
      <p>
        Email <a href={`mailto:${BUSINESS.email}`}>{BUSINESS.email}</a> from the address on
        your account, with your organisation name.
      </p>
    </LegalLayout>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Refund />
  </React.StrictMode>,
);
