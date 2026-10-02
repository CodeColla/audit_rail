import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { SIGNUP_URL, SALES_EMAIL } from "../lib/env";
import { PLANS, ADDONS, GST_NOTE, formatINR, type BillingCycle } from "../lib/pricing";
import { cn } from "../lib/ui";
import { PricingFaq } from "./PricingFaq";

const CYCLES: { key: BillingCycle; label: string }[] = [
  { key: "monthly", label: "Monthly" },
  { key: "yearly", label: "Yearly" },
];

export function Pricing() {
  const reduceMotion = useReducedMotion();
  const [cycle, setCycle] = useState<BillingCycle>("monthly");

  const rise = (delay: number) => ({
    initial: reduceMotion ? undefined : { opacity: 0, y: 16 },
    whileInView: reduceMotion ? undefined : { opacity: 1, y: 0 },
    viewport: { once: true },
    transition: { duration: 0.45, delay: reduceMotion ? 0 : delay, ease: "easeOut" as const },
  });

  return (
    <section id="pricing" className="px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-5xl">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-semibold text-ink sm:text-display">
            Start free. Upgrade when you're running audits, not trying the product.
          </h2>
          <p className="mt-4 text-body text-txt2">
            No card to sign up — pricing is per organisation{GST_NOTE && `, ${GST_NOTE}`}.
          </p>
        </div>

        <div className="mt-8 flex justify-center">
          <div className="inline-flex rounded-xl border border-hair bg-paper p-1">
            {CYCLES.map((c) => (
              <button
                key={c.key}
                type="button"
                onClick={() => setCycle(c.key)}
                className={cn(
                  "rounded-lg px-4 py-1.5 text-sm font-semibold transition-colors",
                  cycle === c.key ? "bg-ink text-paper" : "text-txt2 hover:text-ink",
                )}
              >
                {c.label}
                {c.key === "yearly" && <span className="ml-1.5 text-accent">· 2 months free</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="mx-auto mt-10 grid grid-cols-1 gap-6 sm:grid-cols-3">
          {PLANS.map((plan, i) => {
            const price = plan.price[cycle];
            return (
              <motion.div
                key={plan.code}
                {...rise(i * 0.08)}
                className={cn(
                  "relative flex flex-col rounded-xl border bg-paper p-7 shadow-card",
                  plan.highlight ? "border-accent" : "border-hair",
                )}
              >
                {plan.highlight && (
                  <span className="absolute -top-3 left-7 rounded-full bg-accent px-3 py-1 text-label font-semibold uppercase tracking-wide text-paper">
                    Most popular
                  </span>
                )}
                <p className="text-title font-semibold text-ink">{plan.name}</p>
                <p className="mt-2 flex items-baseline gap-1">
                  {price === null ? (
                    <span className="text-2xl font-semibold text-ink">Custom</span>
                  ) : price === 0 ? (
                    <span className="text-2xl font-semibold text-ink">₹0</span>
                  ) : (
                    <>
                      <span className="text-2xl font-semibold text-ink">{formatINR(price)}</span>
                      <span className="text-sm text-txt3">/ {cycle === "monthly" ? "mo" : "yr"}</span>
                    </>
                  )}
                </p>
                <p className="mt-2 text-sm text-txt2">{plan.priceNote}</p>
                <hr className="my-6 border-hair" />
                <ul className="flex flex-1 flex-col gap-3">
                  {(
                    [
                      ["Active audits", plan.limits.activeAudits],
                      ["Storage", plan.limits.storage],
                      ["Team members", plan.limits.members],
                      ["Guest auditors", plan.limits.guests],
                      ["Support", plan.support],
                    ] as const
                  ).map(([k, v]) => (
                    <li key={k} className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="text-txt2">{k}</span>
                      <span className={cn("font-semibold", plan.highlight ? "text-accent" : "text-ink")}>
                        {v}
                      </span>
                    </li>
                  ))}
                </ul>
                {plan.code === "enterprise" ? (
                  <a
                    href={`mailto:${SALES_EMAIL}?subject=${encodeURIComponent("Auditrail Enterprise")}`}
                    className="mt-6 rounded-xl border border-hair px-5 py-2.5 text-center text-sm font-semibold text-ink transition-colors hover:border-ink/30"
                  >
                    Talk to sales
                  </a>
                ) : (
                  <a
                    href={plan.code === "pro" ? `${SIGNUP_URL}?plan=pro` : SIGNUP_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={cn(
                      "mt-6 rounded-xl px-5 py-2.5 text-center text-sm font-semibold transition-colors",
                      plan.highlight
                        ? "bg-accent text-paper hover:bg-accent-hover"
                        : "border border-hair text-ink hover:border-ink/30",
                    )}
                  >
                    {plan.code === "free" ? "Start free" : "Start free, upgrade anytime"}
                  </a>
                )}
              </motion.div>
            );
          })}
        </div>

        <p className="mx-auto mt-8 max-w-2xl text-center text-sm text-txt2">
          Need more room on Pro? Add{" "}
          {ADDONS.map((a, i) => (
            <span key={a.key}>
              {i > 0 && " or "}
              <span className="font-semibold text-ink">
                {a.label} for {formatINR(a.price[cycle])}/{cycle === "monthly" ? "mo" : "yr"}
              </span>
            </span>
          ))}
          .
        </p>

        <PricingFaq />
      </div>
    </section>
  );
}
