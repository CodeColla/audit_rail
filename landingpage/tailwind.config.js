/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // SR brand — copied verbatim from webui/tailwind.config.js. No shared config exists
        // between the two apps, so this stays hand-synced; if the brand palette ever changes,
        // change it in both places.
        ink: "#0E1A2B",
        accent: "#F97316",
        "accent-hover": "#EA6A0E",
        paper: "#FFFFFF",
        canvas: "#F4F5F6",
        txt2: "#5B6573",
        txt3: "#9AA1AB",
        hair: "#D5D9DE",
        bd: "#E4E7EB",
        // semantic (muted, distinct from the accent)
        ok: "#0F7A55", "ok-bg": "#E6F4EE",
        warn: "#A06A10", "warn-bg": "#FBF0D9",
        bad: "#B23A30", "bad-bg": "#FBE9E6",
        na: "#5B6573", "na-bg": "#ECEEF1",
        info: "#2F5D8C", "info-bg": "#E8F0F8",
      },
      // `-apple-system` first resolves to genuine SF Pro on Apple hardware; everyone else
      // gets self-hosted Inter (via @fontsource-variable/inter — see src/main.tsx), never a
      // Google Fonts link. Same stack as webui/tailwind.config.js.
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Inter Variable"', 'Inter',
               '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      // The 8-step scale below (display..micro) is copied verbatim from webui/tailwind.config.js
      // — kept identical so any shared component (Brand.tsx's Wordmark/PoweredBy) renders the
      // same. `hero` is NEW and landing-only: webui's own Brand.tsx deliberately has "no display
      // font" because 28px is enough for any product screen; a marketing hero needs a bigger
      // numeral than the product ever does. Scoped to this config, so it never touches webui's
      // own type scale or contradicts that decision there.
      fontSize: {
        hero:     ["56px", { lineHeight: "1.08", letterSpacing: "-0.024em" }],
        display:  ["28px", { lineHeight: "1.15", letterSpacing: "-0.021em" }],
        title:    ["20px", { lineHeight: "1.25", letterSpacing: "-0.017em" }],
        subtitle: ["16px", { lineHeight: "1.35", letterSpacing: "-0.011em" }],
        body:     ["14px", { lineHeight: "1.5",  letterSpacing: "-0.006em" }],
        sm:       ["13px", { lineHeight: "1.45", letterSpacing: "-0.003em" }],
        label:    ["12px", { lineHeight: "1.4",  letterSpacing: "0em" }],
        caption:  ["11px", { lineHeight: "1.35", letterSpacing: "0.005em" }],
        micro:    ["10px", { lineHeight: "1.3",  letterSpacing: "0.06em" }],
      },
      boxShadow: {
        card: "0 1px 2px rgba(14,26,43,.04)",
        drawer: "-10px 0 34px rgba(14,26,43,.18)",
      },
      borderRadius: { xl: "10px" },
    },
  },
  plugins: [],
};
