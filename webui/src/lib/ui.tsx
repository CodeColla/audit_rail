import { ReactNode, useEffect } from "react";
import { extendTailwindMerge } from "tailwind-merge";
import clsx, { ClassValue } from "clsx";

/**
 * tailwind-merge has to be TOLD about the P6 type scale, or it silently deletes text colours.
 *
 * It resolves conflicts by class group, and its built-in `font-size` group only knows the
 * stock keys (`text-xs`, `text-sm`, `text-base`…). An unrecognised `text-*` is assumed to be a
 * text COLOUR — so `cn("text-white", "text-caption")` saw two colours, kept the last, and
 * dropped `text-white`. Measured, not guessed: the avatar tiles rendered ink-on-ink,
 * `color: rgb(14,26,43)` against `background: rgb(14,26,43)`, with the initials invisible.
 *
 * Every custom size token has to be listed here. Adding one to tailwind.config.js without
 * adding it here reintroduces exactly this bug, and it fails silently.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["display", "title", "subtitle", "body", "label", "caption", "micro"] }],
    },
  },
});

export const cn = (...a: ClassValue[]) => twMerge(clsx(a));

// status → pill styling. Any unknown value falls back to neutral.
const PILL: Record<string, string> = {
  ok: "text-ok bg-ok-bg", yes: "text-ok bg-ok-bg", valid: "text-ok bg-ok-bg",
  compliant: "text-ok bg-ok-bg", answered: "text-ok bg-ok-bg", validated: "text-ok bg-ok-bg",
  active: "text-ok bg-ok-bg",
  warn: "text-warn bg-warn-bg", partial: "text-warn bg-warn-bg", expiring: "text-warn bg-warn-bg",
  due_soon: "text-warn bg-warn-bg", conditional: "text-warn bg-warn-bg", ask_pending: "text-warn bg-warn-bg",
  bad: "text-bad bg-bad-bg", no: "text-bad bg-bad-bg", expired: "text-bad bg-bad-bg",
  overdue: "text-bad bg-bad-bg", non_compliant: "text-bad bg-bad-bg", high: "text-bad bg-bad-bg",
  info: "text-info bg-info-bg", in_review: "text-info bg-info-bg", submitted: "text-info bg-info-bg",
  applicable: "text-info bg-info-bg", auditor: "text-info bg-info-bg", medium: "text-warn bg-warn-bg",
  na: "text-na bg-na-bg", draft: "text-na bg-na-bg", open: "text-na bg-na-bg",
  not_applicable: "text-na bg-na-bg", pending: "text-na bg-na-bg", low: "text-na bg-na-bg",
};

export function Pill({ tone, children }: { tone?: string; children: ReactNode }) {
  const key = String(tone ?? children).toLowerCase().replace(/[ /-]/g, "_");
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] text-caption font-semibold",
      PILL[key] ?? "text-na bg-na-bg")}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("card p-4", className)}>{children}</div>;
}

export function PageHead({ eyebrow, title, lead, action }:
  { eyebrow: string; title: string; lead?: string; action?: ReactNode }) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1 className="h1 mt-1">{title}</h1>
        {lead && <p className="mt-1 max-w-[70ch] text-sm text-txt2">{lead}</p>}
      </div>
      {action}
    </div>
  );
}

/**
 * The column-header treatment (P6) — the thing Sumit named directly: *"columns header
 * feel/font"*.
 *
 * It was `text-micro uppercase tracking-[0.09em]` in grey, duplicated here and in DataTable.
 * At 10px, uppercase and near-neutral, it read as a system label rather than part of the
 * product. Now `caption` weight-600 in `txt2` on the paper surface with a hairline under it:
 * bigger, more legible, and quieter — a header should organise the data, not compete with it.
 * Exported so `DataTable` uses the same string instead of its own copy drifting apart.
 */
export const TH =
  "border-b border-bd bg-paper px-3.5 py-2.5 text-left text-caption font-semibold text-txt2";

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-bd bg-paper">
      <table className="w-full text-sm">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={TH}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export const Td = ({ className, children }: { className?: string; children?: ReactNode }) => (
  <td className={cn("border-b border-bd px-3.5 py-3 align-middle", className)}>{children}</td>
);

export function Bar({ pct, muted }: { pct: number; muted?: boolean }) {
  return (
    <div className="h-2 overflow-hidden rounded-full bg-bd">
      <div className={cn("h-full rounded-full", muted ? "bg-txt3" : "bg-accent")}
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </div>
  );
}

export function Drawer({ open, onClose, title, sub, children }:
  { open: boolean; onClose: () => void; title: ReactNode; sub?: ReactNode; children: ReactNode }) {
  useCloseOnEscape(open, onClose);
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-40 bg-[rgba(14,26,43,0.36)]" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-labelledby="drawer-title"
        className="fixed right-0 top-0 z-50 flex h-full w-[min(560px,94vw)] flex-col overflow-y-auto border-l border-bd bg-canvas shadow-drawer">
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-bd bg-paper px-5 py-4">
          <div className="flex-1">
            {sub && <div className="font-mono text-label font-semibold text-accent">{sub}</div>}
            <h3 id="drawer-title" className="mt-0.5 text-body font-semibold leading-snug">{title}</h3>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="grid h-9 w-9 place-items-center rounded-md border border-bd text-txt2 hover:bg-canvas">✕</button>
        </div>
        <div className="flex flex-col gap-4 p-5">{children}</div>
      </aside>
    </>
  );
}

/**
 * Escape closes the topmost overlay. Both Drawer and Modal were dismissible only by the ✕ or
 * a backdrop click, which is the one keyboard convention every user already knows.
 */
function useCloseOnEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

/**
 * `size` widens the dialog for content that needs it (e.g. the role permission matrix).
 *
 * The body scrolls INSIDE the dialog and the whole thing is capped at 90vh. Without that,
 * a tall dialog centred with `place-items-center` overflows past the top and bottom of the
 * viewport with nothing to scroll — its footer buttons become literally unclickable.
 */
export function Modal({ open, onClose, title, children, size = "md" }:
  { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode;
    size?: "md" | "lg" | "xl" }) {
  useCloseOnEscape(open, onClose);
  if (!open) return null;
  const width = { md: "max-w-md", lg: "max-w-2xl", xl: "max-w-4xl" }[size];
  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[rgba(14,26,43,0.44)] p-4"
      onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="modal-title"
        className={cn("flex max-h-[90vh] w-full flex-col rounded-xl border border-bd bg-paper shadow-drawer", width)}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex shrink-0 items-center justify-between border-b border-bd px-5 py-3.5">
          <h3 id="modal-title" className="text-body font-semibold">{title}</h3>
          <button onClick={onClose} aria-label="Close"
            className="grid h-8 w-8 place-items-center rounded-md border border-bd text-txt2 hover:bg-canvas">✕</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}

// segmented response picker (yes / partial / no / na)
export function Segment({ value, onChange, options }:
  { value: string; onChange: (v: string) => void; options: { v: string; label: string; tone: string }[] }) {
  return (
    <div className="flex gap-1.5">
      {options.map((o) => (
        <button key={o.v} type="button" onClick={() => onChange(o.v)}
          className={cn("rounded-md border px-3 py-1.5 text-label font-semibold",
            value === o.v ? PILL[o.tone] + " border-current" : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * `outline-none` with only a border-colour change was a WCAG 2.4.7 failure: keyboard users got
 * a one-pixel hue shift as their sole focus indicator. The ring restores a visible focus state
 * without the browser's default blue halo, and `focus-visible` keeps it off mouse clicks.
 */
export const inputCls =
  "w-full rounded-md border border-bd px-3 py-2 text-sm outline-none transition-colors " +
  "focus:border-accent focus-visible:ring-2 focus-visible:ring-accent/30";

/**
 * A skeleton, not the word "Loading…". This renders on the first paint of nearly every screen,
 * so it is the first thing a new user sees — a bare word in grey reads as an unfinished app.
 * Shaped like the content that follows (a title, then rows) so the page does not jump.
 */
export const Loading = () => (
  <div role="status" aria-label="Loading" className="animate-pulse">
    <div className="h-6 w-48 rounded-md bg-bd/70" />
    <div className="mt-2 h-3.5 w-80 rounded bg-bd/50" />
    <div className="mt-5 space-y-2 rounded-xl border border-bd bg-paper p-4">
      {[92, 78, 85, 64].map((w, i) => (
        <div key={i} className="h-4 rounded bg-bd/50" style={{ width: `${w}%` }} />
      ))}
    </div>
  </div>
);
export const Empty = ({ children }: { children: ReactNode }) => (
  <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">{children}</div>
);
