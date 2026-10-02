// This page is a SEPARATE deployment from webui — its own container, own port, no shared
// origin — so the sign-up/login CTAs can't assume a relative path resolves anywhere real.
//
// Baked in at BUILD time (Vite statically replaces `import.meta.env.VITE_*` during
// `vite build`), unlike the UI container's API_URL, which `_docker/ui/entrypoint.sh` reads at
// CONTAINER START. Changing any of these means rebuilding the landing image, not just
// restarting it — a known limitation, not an oversight; see docs/phase9/01-sprint-plan.md
// P9-S3 and docs/phase10/01-sprint-plan.md P10-S1.
const APP_URL = (import.meta.env.VITE_APP_URL ?? "https://app.auditrail.srassociates.co").replace(
  /\/+$/,
  "",
);

export const SIGNUP_URL = `${APP_URL}/signup`;
export const LOGIN_URL = `${APP_URL}/login`;

// Enterprise is sales-led, not self-serve (see docs/phase9) — its CTA opens a mail composer
// rather than posting to a waitlist API that doesn't exist. Same build-time-only caveat as
// above. Also the address printed on the legal pages (src/lib/business.ts).
export const SALES_EMAIL = import.meta.env.VITE_SALES_EMAIL ?? "hello@srassociates.co";

// Razorpay won't activate an account without a phone number on the public Contact page
// (req/phase10/00-issue-19.md §2). The owner hasn't provided a real one yet, so this is a
// placeholder rather than an invented number — swap it via VITE_CONTACT_PHONE without
// touching code. Same build-time caveat as above.
export const CONTACT_PHONE = import.meta.env.VITE_CONTACT_PHONE ?? "+91 00000 00000";
