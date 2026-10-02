// Native <details>/<summary> — no JS, no state, no new dependency for four questions.
const FAQS: { q: string; a: string }[] = [
  {
    q: "What counts as an active audit?",
    a: "Any audit that isn't closed yet. Closing one frees up the slot — closed audits stay fully readable and exportable on every plan, and reopening one just checks the same limit as starting a new one.",
  },
  {
    q: "What happens if I hit a plan limit?",
    a: "Nothing you've already done is touched. You just can't start a new audit, reopen a closed one, or upload another file until you upgrade, buy an add-on, or free up room.",
  },
  {
    q: "Are guest auditors free?",
    a: "Yes. Bank and external auditor logins never count toward your plan, on any tier.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes — cancel whenever you like and keep access until the end of the period you've already paid for. Monthly payments aren't refunded; yearly payments get a full refund if you cancel within 7 days of paying.",
  },
  {
    q: "Do I get an invoice?",
    a: "Yes, for every payment. We're not GST-registered yet, so there's no GST line on it today — the price you see is the price you pay.",
  },
];

export function PricingFaq() {
  return (
    <div className="mx-auto mt-16 max-w-2xl">
      <h3 className="text-center text-label font-semibold uppercase tracking-wide text-txt3">
        Questions
      </h3>
      <div className="mt-6 divide-y divide-hair border-y border-hair">
        {FAQS.map((f) => (
          <details key={f.q} className="group py-4">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-sm font-semibold text-ink">
              {f.q}
              <span className="shrink-0 text-txt3 transition-transform group-open:rotate-45">+</span>
            </summary>
            <p className="mt-3 text-sm text-txt2">{f.a}</p>
          </details>
        ))}
      </div>
    </div>
  );
}
