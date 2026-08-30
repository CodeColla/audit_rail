// This page is a SEPARATE deployment from webui — its own container, own port, no shared
// origin — so the sign-up CTA can't assume a relative "/signup" path resolves anywhere real.
//
// Baked in at BUILD time (Vite statically replaces `import.meta.env.VITE_*` during
// `vite build`), unlike the UI container's API_URL, which `_docker/ui/entrypoint.sh` reads at
// CONTAINER START. Changing this means rebuilding the landing image, not just restarting it —
// a known limitation, not an oversight; see docs/phase9/01-sprint-plan.md P9-S3.
export const SIGNUP_URL =
  import.meta.env.VITE_SIGNUP_URL ?? "https://app.auditrail.example.com/signup";

// Enterprise is sales-led, not self-serve (see docs/phase9) — its CTA opens a mail composer
// rather than posting to a waitlist API that doesn't exist. Same build-time-only caveat as
// SIGNUP_URL above.
export const SALES_EMAIL = import.meta.env.VITE_SALES_EMAIL ?? "sales@auditrail.example.com";
