import { Wordmark, PoweredBy } from "./Brand";
import { SALES_EMAIL } from "../lib/env";

// Phase 10: Razorpay won't activate an account without public Terms/Privacy/Refund/Contact
// pages, so those now exist (see src/legal/) and are linked here. Each is its own Vite entry
// — not a route — because this app deliberately has no router (docs/phase9).
const LEGAL_LINKS = [
  { href: "/terms", label: "Terms" },
  { href: "/privacy", label: "Privacy" },
  { href: "/refund", label: "Refund & Cancellation" },
  { href: "/contact", label: "Contact" },
];

export function Footer() {
  return (
    <footer className="border-t border-hair/70 px-4 py-10 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col items-center gap-2 sm:items-start">
          <Wordmark size="sm" />
          <PoweredBy />
        </div>
        <nav className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-txt2 sm:justify-end">
          {LEGAL_LINKS.map((l) => (
            <a key={l.href} href={l.href} className="transition-colors hover:text-ink">
              {l.label}
            </a>
          ))}
          <a href={`mailto:${SALES_EMAIL}`} className="transition-colors hover:text-ink">
            {SALES_EMAIL}
          </a>
        </nav>
      </div>
      <p className="mx-auto mt-6 max-w-6xl text-center text-micro text-txt3 sm:text-left">
        © {new Date().getFullYear()} SR Associates
      </p>
    </footer>
  );
}
