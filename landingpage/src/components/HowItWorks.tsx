import { motion, useReducedMotion } from "framer-motion";

const STEPS = [
  {
    title: "Import the checklist",
    body: "Drop in a customer's .xlsx or .csv questionnaire — every question lands as a row, ready to map.",
  },
  {
    title: "Map to your controls",
    body: "Point each question at the control that already answers it, once, from your control library.",
  },
  {
    title: "Answer with evidence",
    body: "Attach the policy, the screenshot, the log export — whatever proves the control actually works.",
  },
  {
    title: "Reuse on the next audit",
    body: "The next questionnaire that asks the same thing is a review of what you already said, not a rewrite.",
  },
];

export function HowItWorks() {
  const reduceMotion = useReducedMotion();

  return (
    <section id="how-it-works" className="px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-5xl">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-semibold text-ink sm:text-display">How it works</h2>
          <p className="mt-4 text-body text-txt2">
            Built for vendors who serve regulated customers: one customer sends a 200-question
            security questionnaire, their auditor follows up, and next quarter another customer
            asks the same things in different words.
          </p>
        </div>
        <div className="relative mt-16">
          <div aria-hidden className="absolute left-0 right-0 top-5 hidden h-px bg-hair sm:block" />
          <motion.div
            aria-hidden
            className="absolute left-0 top-5 hidden h-px bg-accent sm:block"
            initial={reduceMotion ? { width: "100%" } : { width: "0%" }}
            whileInView={{ width: "100%" }}
            viewport={{ once: true }}
            transition={{ duration: reduceMotion ? 0 : 1.1, ease: "easeInOut" }}
          />
          <div className="grid grid-cols-1 gap-10 sm:grid-cols-4 sm:gap-6">
            {STEPS.map((s, i) => (
              <motion.div
                key={s.title}
                initial={reduceMotion ? undefined : { opacity: 0, y: 16 }}
                whileInView={reduceMotion ? undefined : { opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.4, delay: reduceMotion ? 0 : i * 0.12, ease: "easeOut" }}
              >
                <div className="relative z-10 flex h-10 w-10 items-center justify-center rounded-full border border-hair bg-paper text-sm font-semibold text-ink shadow-card">
                  {i + 1}
                </div>
                <h3 className="mt-4 text-subtitle font-semibold text-ink">{s.title}</h3>
                <p className="mt-2 text-sm text-txt2">{s.body}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
