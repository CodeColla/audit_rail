import { motion, useReducedMotion } from "framer-motion";
import iamLogo from "../assets/clients/iam.png";
import kiamLogo from "../assets/clients/kiam.png";
import iesgLabsLogo from "../assets/clients/iesg-labs.png";

// Named, not invented: IAM is already the in-house user per README.md's "Project status", and
// IESG Labs is the vendor behind it (see Brand.tsx's VENDOR="SR"). Logos pulled from each
// company's own site (smartiam.in, kiam.in, iesglabs.com) — real marks, not placeholders.
//
// "100+ customers" was considered and dropped: that reads as 100+ companies in this market,
// and there are 3. "100+ users" is the honest version of the same underlying number.
const CLIENTS = [
  { name: "IAM", logo: iamLogo },
  { name: "KIAM", logo: kiamLogo },
  { name: "IESG Labs", logo: iesgLabsLogo },
];

export function TrustedBy() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="border-y border-hair/70 bg-canvas px-4 py-14 sm:px-6">
      <motion.div
        initial={reduceMotion ? undefined : { opacity: 0, y: 12 }}
        whileInView={reduceMotion ? undefined : { opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.45, ease: "easeOut" }}
        className="mx-auto max-w-4xl text-center"
      >
        <p className="text-label font-semibold uppercase tracking-wide text-txt3">
          100+ users, in early use at
        </p>
        <div className="mt-7 flex flex-wrap items-center justify-center gap-x-14 gap-y-6">
          {CLIENTS.map((c) => (
            <img
              key={c.name}
              src={c.logo}
              alt={c.name}
              className="h-8 w-auto grayscale opacity-60 transition-all duration-200 hover:grayscale-0 hover:opacity-100 sm:h-9"
            />
          ))}
        </div>
      </motion.div>
    </section>
  );
}
