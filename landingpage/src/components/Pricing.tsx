import { motion, useReducedMotion } from "framer-motion";
import { SIGNUP_URL, SALES_EMAIL } from "../lib/env";

const TRIAL_LIMITS = [
  { k: "Active audits", v: "2" },
  { k: "Audit points", v: "200 total" },
  { k: "Evidence storage", v: "100 MB" },
  { k: "Documents", v: "10" },
  { k: "Seats", v: "3" },
  { k: "Support", v: "Onboarding email" },
];

const ENTERPRISE_LIMITS = [
  { k: "Active audits", v: "Unlimited" },
  { k: "Audit points", v: "Unlimited" },
  { k: "Evidence storage", v: "Scoped to contract" },
  { k: "Documents", v: "Unlimited" },
  { k: "Seats", v: "Scoped to contract" },
  { k: "Support", v: "Dedicated CSM + SSO" },
];

export function Pricing() {
  const reduceMotion = useReducedMotion();
  const rise = (delay: number) => ({
    initial: reduceMotion ? undefined : { opacity: 0, y: 16 },
    whileInView: reduceMotion ? undefined : { opacity: 1, y: 0 },
    viewport: { once: true },
    transition: { duration: 0.45, delay: reduceMotion ? 0 : delay, ease: "easeOut" as const },
  });

  return (
    <section id="pricing" className="px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-4xl">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-semibold text-ink sm:text-display">
            Try it on a real audit, then talk to us
          </h2>
          <p className="mt-4 text-body text-txt2">
            No self-serve rate card — every team that buys in talks to us first, so pricing
            fits the audits you actually run.
          </p>
        </div>

        <div className="mx-auto mt-14 grid grid-cols-1 gap-6 sm:grid-cols-2">
          <motion.div {...rise(0)} className="flex flex-col rounded-xl border border-hair bg-paper p-7 shadow-card">
            <p className="text-title font-semibold text-ink">Trial</p>
            <p className="mt-2 flex items-baseline gap-1">
              <span className="text-2xl font-semibold text-ink">30 days</span>
            </p>
            <p className="mt-2 text-sm text-txt2">
              Everything unlocked, capped for a fair evaluation window.
            </p>
            <hr className="my-6 border-hair" />
            <ul className="flex flex-1 flex-col gap-3">
              {TRIAL_LIMITS.map((l) => (
                <li key={l.k} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-txt2">{l.k}</span>
                  <span className="font-semibold text-ink">{l.v}</span>
                </li>
              ))}
            </ul>
            <p className="mt-6 border-t border-dashed border-hair pt-4 text-xs text-txt3">
              After day 30: read-only export of your work, until you talk to sales.
            </p>
            <a
              href={SIGNUP_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-6 rounded-xl border border-hair px-5 py-2.5 text-center text-sm font-semibold text-ink transition-colors hover:border-ink/30"
            >
              Start free trial
            </a>
          </motion.div>

          <motion.div
            {...rise(0.08)}
            className="relative flex flex-col rounded-xl border border-accent bg-paper p-7 shadow-card"
          >
            <span className="absolute -top-3 left-7 rounded-full bg-accent px-3 py-1 text-label font-semibold uppercase tracking-wide text-paper">
              The only paid plan
            </span>
            <p className="text-title font-semibold text-ink">Enterprise</p>
            <p className="mt-2 flex items-baseline gap-1">
              <span className="text-2xl font-semibold text-ink">Custom</span>
              <span className="text-sm text-txt3">priced per deal</span>
            </p>
            <p className="mt-2 text-sm text-txt2">Scoped to your team, not sold off a rate card.</p>
            <hr className="my-6 border-hair" />
            <ul className="flex flex-1 flex-col gap-3">
              {ENTERPRISE_LIMITS.map((l) => (
                <li key={l.k} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-txt2">{l.k}</span>
                  <span className="font-semibold text-accent">{l.v}</span>
                </li>
              ))}
            </ul>
            <a
              href={`mailto:${SALES_EMAIL}?subject=${encodeURIComponent("Auditrail Enterprise")}`}
              className="mt-6 rounded-xl bg-accent px-5 py-2.5 text-center text-sm font-semibold text-paper transition-colors hover:bg-accent-hover"
            >
              Talk to sales
            </a>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
