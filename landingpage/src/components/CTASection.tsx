import { SIGNUP_URL } from "../lib/env";

// Visual recipe ported from webui/src/components/AuthLayout.tsx's Panel(): ink background,
// masked rail texture, one accent bloom.
export function CTASection() {
  return (
    <section className="relative overflow-hidden bg-ink px-4 py-24 sm:px-6">
      <div aria-hidden className="hero-rails-dark pointer-events-none absolute inset-0" />
      <div
        aria-hidden
        className="pointer-events-none absolute -left-32 -top-32 h-96 w-96 rounded-full bg-accent opacity-[0.13] blur-3xl"
      />
      <div className="relative mx-auto max-w-2xl text-center">
        <h2 className="text-2xl font-semibold text-paper sm:text-display">
          Answer once. Reuse across every audit.
        </h2>
        <p className="mt-4 text-body text-[#8794A6]">
          Controls, evidence, policies and attestations in one place — so the next compliance
          questionnaire is a review, not a rewrite.
        </p>
        <div className="mt-8">
          <a
            href={SIGNUP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block rounded-xl bg-accent px-6 py-3 text-sm font-semibold text-paper shadow-card transition-colors hover:bg-accent-hover"
          >
            Get started
          </a>
        </div>
      </div>
    </section>
  );
}
