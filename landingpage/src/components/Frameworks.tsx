import { motion, useReducedMotion } from "framer-motion";
import { Globe2, ShieldCheck, Landmark } from "lucide-react";

// Generic mark per framework, not a reproduction of any certification body's own logo —
// ISO/AICPA/RBI trademarks aren't ours to use, so these are our own iconography, styled the
// way the reference site (vanta.com) badges its framework row: one glyph, one name.
const FRAMEWORKS = [
  { name: "ISO 27001:2022", detail: "Annex A controls, mapped clause by clause", icon: Globe2 },
  { name: "SOC 2", detail: "Trust Services Criteria, evidenced and current", icon: ShieldCheck },
  { name: "RBI-ITO", detail: "IT outsourcing directions for regulated vendors", icon: Landmark },
];

export function Frameworks() {
  const reduceMotion = useReducedMotion();

  return (
    <section id="frameworks" className="border-y border-hair/70 bg-canvas px-4 py-24 sm:px-6">
      <div className="mx-auto max-w-4xl text-center">
        <h2 className="text-2xl font-semibold text-ink sm:text-display">
          One control library, every certification
        </h2>
        <p className="mx-auto mt-4 max-w-2xl text-body text-txt2">
          95 canonical controls across 16 domains, each tagged with the framework clauses it
          satisfies — so one control answers ISO, SOC 2 and RBI-ITO at once, instead of a
          separate control set per framework.
        </p>
        <div className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-3">
          {FRAMEWORKS.map((f, i) => {
            const Icon = f.icon;
            return (
              <motion.div
                key={f.name}
                initial={reduceMotion ? undefined : { opacity: 0, y: 16 }}
                whileInView={reduceMotion ? undefined : { opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.45, delay: reduceMotion ? 0 : i * 0.08, ease: "easeOut" }}
                className="rounded-xl border border-hair bg-paper p-6 text-left shadow-card"
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-full bg-accent/10 text-accent">
                  <Icon size={22} strokeWidth={2} aria-hidden />
                </div>
                <p className="mt-4 text-title font-semibold text-ink">{f.name}</p>
                <p className="mt-1 text-sm text-txt2">{f.detail}</p>
              </motion.div>
            );
          })}
        </div>
        <p className="mt-8 text-sm text-txt3">— and more, as the control library grows.</p>
      </div>
    </section>
  );
}
