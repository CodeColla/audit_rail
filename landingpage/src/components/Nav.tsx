import { Wordmark } from "./Brand";
import { SIGNUP_URL } from "../lib/env";

const LINKS = [
  { href: "#features", label: "Product" },
  { href: "#frameworks", label: "Frameworks" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#pricing", label: "Pricing" },
];

export function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-hair/70 bg-paper/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <a href="#top" className="flex items-center">
          <Wordmark size="md" />
        </a>
        <nav className="hidden items-center gap-8 md:flex">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} className="text-sm text-txt2 transition-colors hover:text-ink">
              {l.label}
            </a>
          ))}
        </nav>
        <a
          href={SIGNUP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-xl bg-ink px-4 py-2 text-sm font-semibold text-paper transition-colors hover:bg-ink/90"
        >
          Get started
        </a>
      </div>
    </header>
  );
}
