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
      fontFamily: {
        sans: ["Space Grotesk", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["Space Mono", "ui-monospace", "SFMono-Regular", "monospace"],
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
