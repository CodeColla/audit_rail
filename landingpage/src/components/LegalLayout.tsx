import type { ReactNode } from "react";
import { Wordmark, PoweredBy } from "./Brand";
import { BUSINESS } from "../lib/business";

// Shared chrome for the standalone legal pages (terms/privacy/refund/contact) — each is its
// own Vite entry (see vite.config.ts), not a route, because this app deliberately has no
// router (docs/phase9). A thin header linking back to "/" plus a signature footer is enough;
// these pages don't need Nav's anchor links or CTA button.
export function LegalLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-paper">
      <header className="border-b border-hair/70 px-4 py-5 sm:px-6">
        <a href="/" className="inline-flex items-center">
          <Wordmark size="md" />
        </a>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-16 sm:px-6">
        <h1 className="text-2xl font-semibold text-ink sm:text-display">{title}</h1>
        <div className="legal-content mt-8 text-sm leading-relaxed text-txt2">{children}</div>
      </main>
      <footer className="border-t border-hair/70 px-4 py-10 sm:px-6">
        <div className="mx-auto flex max-w-2xl flex-col items-center gap-2 text-center">
          <PoweredBy />
          <p className="text-micro text-txt3">
            {BUSINESS.legalName}, {BUSINESS.address}
          </p>
        </div>
      </footer>
    </div>
  );
}
