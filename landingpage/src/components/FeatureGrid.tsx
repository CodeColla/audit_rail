import { motion, useReducedMotion } from "framer-motion";

// Copy sourced directly from README.md's module table — lightly tightened for marketing
// tone, not rewritten in substance.
const FEATURES = [
  {
    title: "Audits",
    body: "Import a customer's checklist and map its questions onto your controls, then answer it — reusing answers you've already given. Auditors get scoped guest access to review and raise findings.",
  },
  {
    title: "Controls",
    body: "One library of canonical controls, each tagged with the framework clauses it satisfies. One control, many certifications — not a separate set per framework.",
  },
  {
    title: "Documents",
    body: "Policies and registers authored in the app: a rich-text editor for prose, a spreadsheet surface for registers. Versioned, approved, published as a frozen record.",
  },
  {
    title: "Evidence",
    body: "The artifacts that prove a control works, with validity windows so you know what's gone stale before the auditor does.",
  },
  {
    title: "Tasks",
    body: "The recurring half of compliance — quarterly access reviews, annual policy reviews — generated on a schedule and chased when overdue.",
  },
  {
    title: "Registers",
    body: "Risks, assets, data inventory, third parties and incidents, each importable in bulk from a spreadsheet.",
  },
  {
    title: "People",
    body: "Who works here, what they can see, and who has attested to which policy. Attestation goes out as a magic link that needs no account.",
  },
];

export function FeatureGrid() {
  const reduceMotion = useReducedMotion();

  return (
    <section id="features" className="px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-semibold text-ink sm:text-display">
            Everything an audit touches, in one place
          </h2>
          <p className="mt-4 text-body text-txt2">
            Seven modules, one tenant-scoped workspace — no spreadsheet graveyard, no
            re-answering the same question every quarter.
          </p>
        </div>
        <div className="mt-14 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => (
            <motion.div
              key={f.title}
              initial={reduceMotion ? undefined : { opacity: 0, y: 16 }}
              whileInView={reduceMotion ? undefined : { opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.45, delay: reduceMotion ? 0 : (i % 3) * 0.06, ease: "easeOut" }}
              className="rounded-xl border border-hair bg-paper p-6 shadow-card"
            >
              <h3 className="text-title font-semibold text-ink">{f.title}</h3>
              <p className="mt-2 text-body text-txt2">{f.body}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
