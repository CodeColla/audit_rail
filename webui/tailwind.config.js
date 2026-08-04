/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // SR brand
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
      // P6-S1. Space Grotesk is a geometric DISPLAY face; it was set on body and inherited by
      // every table, form and figure in the app, which is why the portal read as a template.
      //
      // `-apple-system` first is deliberate: it resolves to genuine SF Pro on Mac/iPhone/iPad
      // (Apple's own site does exactly this) and SF Pro cannot be licensed for the web by any
      // other means. Everyone else gets self-hosted Inter — designed close to SF, SIL OFL —
      // rather than falling through to Segoe UI, so the product looks the same everywhere.
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Inter Variable"', 'Inter',
               '"Segoe UI"', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
      },

      // A real type scale. The app carried 20 hardcoded sizes across 645 usages, several a
      // HALF PIXEL apart (12/12.5/13/13.5, 9/9.5/10/10.5) — no single screen looked wrong, but
      // collectively it read as unresolved. Each token carries its own line-height AND
      // letter-spacing: tightening at display sizes and opening up at caption sizes is most of
      // what separates type that looks designed from type that looks assembled.
      //
      // Near-duplicates COLLAPSE rather than everything being resized — 13px is still 13px —
      // so the jitter goes without reflowing every table in the product.
      fontSize: {
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
