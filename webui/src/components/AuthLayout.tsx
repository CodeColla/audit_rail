import { ReactNode } from "react";
import { PoweredBy, Wordmark } from "./Brand";
import { cn } from "../lib/ui";

/**
 * The shell every signed-out page shares (P6-S6): login, signup, the auditor entry point and
 * the forced password change.
 *
 * It exists because the brand lockup was previously copy-pasted character for character into
 * Login and Signup, and absent entirely from the other two — so a customer's auditor's first
 * impression of the product was a bare sentence on a grey rectangle.
 *
 * Two shapes:
 *
 *   `split`   — an ink panel beside the form. Only /login uses it. A login form is four
 *               elements tall, which leaves half the screen doing nothing; the panel is what
 *               that space is for. Below `lg` the panel is gone and this degrades exactly to
 *               `centred`, so there is one responsive story, not two.
 *   `centred` — the card on the same surface. Signup keeps this deliberately: it runs seven
 *               fields, and in a split at 1280x720 the form would scroll. Sharing the surface
 *               and the wordmark is what stops it looking orphaned; it does not need the panel.
 */
export function AuthLayout({ variant = "centred", width = "sm", children }: {
  variant?: "centred" | "split";
  width?: "sm" | "md";
  children: ReactNode;
}) {
  const card = (
    <div className={cn("relative w-full", width === "md" ? "max-w-md" : "max-w-sm")}>
      {/* In the split, the panel carries the wordmark, so the form side must not repeat it. */}
      {variant === "centred" && (
        <div className="mb-6 flex items-center"><Wordmark size="lg" /></div>
      )}
      {children}
      <PoweredBy className="mt-6 text-center" />
    </div>
  );

  if (variant === "split") {
    return (
      <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Panel />
        <div className="auth-surface relative grid place-items-center overflow-hidden px-4 py-10">
          <Rails />
          {/* The wordmark rides along on narrow screens, where the panel is hidden. */}
          <div className="relative w-full max-w-sm">
            <div className="mb-6 flex items-center lg:hidden"><Wordmark size="lg" /></div>
            {children}
            <PoweredBy className="mt-6 text-center" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-surface relative grid min-h-screen place-items-center overflow-hidden px-4 py-10">
      <Rails />
      {card}
    </div>
  );
}

/** Decoration only: out of the accessibility tree, out of hit-testing, out of flow — so it
 *  cannot move a pixel of the form. `overflow-hidden` on the parent is what stops the
 *  bottom-right bloom from creating a horizontal scrollbar on a phone. */
const Rails = () => (
  <div aria-hidden className="auth-rails pointer-events-none absolute inset-0" />
);

/** The ink half of /login. Hidden below `lg` rather than stacked: stacked, it would push the
 *  form below the fold on a phone, which is the one thing a login page must never do. */
function Panel() {
  return (
    <div className="relative hidden overflow-hidden bg-ink px-12 py-14 lg:flex lg:flex-col">
      <div aria-hidden className="auth-rails-dark pointer-events-none absolute inset-0" />
      {/* A single accent bloom, so the panel is not a flat rectangle either. */}
      <div aria-hidden className="pointer-events-none absolute -left-32 -top-32 h-96 w-96
                                  rounded-full bg-accent opacity-[0.13] blur-3xl" />
      <div className="relative flex h-full flex-col">
        <Wordmark size="lg" tone="dark" />
        <div className="mt-auto max-w-md">
          {/* The promise the product already makes in index.html's meta description — said
              once, in one voice, rather than invented again here. */}
          <p className="text-display font-semibold leading-tight text-paper">
            Answer once.<br />Reuse across every audit.
          </p>
          <p className="mt-4 text-body text-[#8794A6]">
            Controls, evidence, policies and attestations in one place — so the next bank
            questionnaire is a review, not a rewrite.
          </p>
        </div>
      </div>
    </div>
  );
}
