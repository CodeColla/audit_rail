import { cn } from "../lib/ui";

/**
 * The product's name, in one place (P6-S6).
 *
 * It used to be the other way round. Every surface rendered **SR** — the vendor — at 20-28px
 * bold, with `audit_rail` beside it in monospace, at 11px, in `text-txt3`, the lightest grey
 * the palette has. The thing being sold was styled as a subordinate label of the company
 * selling it, and the same three-span lockup was copy-pasted into four components.
 *
 * Now: **Audit Rail** is the wordmark, SR is a line of type in the footer, and both live here
 * so the next surface that needs one does not become a fifth copy.
 *
 * `audit_rail` survives ONLY as an identifier a human never reads — the Postgres GUC
 * `audit_rail.tenant_id` that every RLS policy consults, the database and docker volume names,
 * the npm package. Renaming any of those would break the product; renaming this file's strings
 * cannot.
 *
 * Depends on nothing but `cn`, so the unauthenticated `Sign.tsx` and the guest `AuditorApp.tsx`
 * can use it without pulling in auth.
 */

export const PRODUCT_NAME = "Audit Rail";
export const VENDOR = "SR";

type Size = "sm" | "md" | "lg";
type Tone = "light" | "dark";

//: Sizes map onto the existing type scale — there is no display font and P6-S1 removed the one
//: there was. `60-typography-s1.spec.ts` pins that, and Inter Variable already carries 700 from
//: the single woff2 imported in main.tsx, so the wordmark costs no new byte.
const WORD: Record<Size, string> = {
  sm: "text-subtitle",   // 16px — the sidebar
  md: "text-title",      // 20px — the dark auditor bar
  lg: "text-display",    // 28px — the signed-out pages
};
const DOT: Record<Size, string> = {
  sm: "h-2 w-2",
  md: "h-[9px] w-[9px]",
  lg: "h-[11px] w-[11px]",
};

/**
 * The product lockup: the accent square, then the name.
 *
 * **No `tracking-[-0.04em]`.** The old lockup carried it because "SR" is two capitals that need
 * manual tightening at 20-28px. The type scale already bakes -0.011/-0.017/-0.021em, which is
 * right for a two-word wordmark; piling -0.04em on top collides the "il" in "Rail".
 *
 * **The accent square leads, and is now the product's mark.** It is the only non-typographic
 * element in the whole brand and it already recurs as the active-nav indicator — once "SR" goes
 * the product would otherwise have no mark at all. Leading rather than trailing also drops the
 * `mb-3`/`mb-3.5` nudge every old copy needed to lift a trailing dot to cap height.
 */
export function Wordmark({ size = "sm", tone = "light", className }: {
  size?: Size; tone?: Tone; className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className={cn("shrink-0 rounded-[2px] bg-accent", DOT[size])} aria-hidden />
      <span className={cn(WORD[size], "font-bold whitespace-nowrap",
                          tone === "dark" ? "text-paper" : "text-ink")}>
        {PRODUCT_NAME}
      </span>
    </span>
  );
}

/**
 * Vendor attribution. A line of type, never a logo.
 *
 * `text-txt2`, not `text-txt3`: #9AA1AB on either canvas or paper is about 2.3:1 and fails AA,
 * while #5B6573 clears 5:1. Small print is still print.
 */
export function PoweredBy({ className }: { className?: string }) {
  return (
    <p className={cn("text-micro text-txt2", className)}>
      {/* `tracking-normal` undoes text-micro's +0.06em: at two capitals that reads as
          "S R" rather than a name. */}
      Powered by <span className="font-semibold tracking-normal">{VENDOR}</span> · © {new Date().getFullYear()}
    </p>
  );
}
