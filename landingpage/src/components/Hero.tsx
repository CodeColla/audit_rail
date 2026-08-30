import { motion, useReducedMotion } from "framer-motion";
import { SIGNUP_URL } from "../lib/env";

export function Hero() {
  const reduceMotion = useReducedMotion();

  const rise = (delay: number) =>
    reduceMotion
      ? {}
      : {
          initial: { opacity: 0, y: 16 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.5, delay, ease: "easeOut" as const },
        };

  return (
    <section id="top" className="hero-surface relative overflow-hidden px-4 py-24 sm:px-6 sm:py-32">
      <div aria-hidden className="hero-rails pointer-events-none absolute inset-0" />
      <div className="relative mx-auto max-w-3xl text-center">
        <motion.p
          {...rise(0)}
          className="mx-auto inline-block rounded-full border border-hair bg-paper px-3 py-1 text-label font-semibold uppercase tracking-wide text-accent"
        >
          Automate Compliance
        </motion.p>
        <motion.h1
          {...rise(0.08)}
          className="mt-6 text-4xl font-semibold leading-tight tracking-tight text-ink sm:text-hero"
        >
          Answer every compliance questionnaire once,<br className="hidden sm:block" /> reuse it
          across every audit.
        </motion.h1>
        <motion.p {...rise(0.16)} className="mx-auto mt-6 max-w-xl text-body text-txt2 sm:text-subtitle">
          A compliance and audit workspace for teams who get audited a lot — with the
          controls, evidence, policies and attestations that back every answer.
        </motion.p>
        <motion.div {...rise(0.24)} className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <a
            href={SIGNUP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full rounded-xl bg-accent px-6 py-3 text-center text-sm font-semibold text-paper shadow-card transition-colors hover:bg-accent-hover sm:w-auto"
          >
            Get started
          </a>
          <a
            href="#how-it-works"
            className="w-full rounded-xl border border-hair bg-paper px-6 py-3 text-center text-sm font-semibold text-ink transition-colors hover:border-ink/30 sm:w-auto"
          >
            See how it works
          </a>
        </motion.div>
      </div>
    </section>
  );
}
