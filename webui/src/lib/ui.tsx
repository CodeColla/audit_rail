import { ReactNode, useEffect } from "react";
import { twMerge } from "tailwind-merge";
import clsx, { ClassValue } from "clsx";

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
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] text-[11.5px] font-semibold",
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
        {lead && <p className="mt-1 max-w-[70ch] text-[13.5px] text-txt2">{lead}</p>}
      </div>
      {action}
    </div>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-bd bg-paper">
      <table className="w-full text-[13px]">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className="border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.09em] text-txt3">
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
            {sub && <div className="font-mono text-[12px] font-semibold text-accent">{sub}</div>}
            <h3 id="drawer-title" className="mt-0.5 text-[15px] font-semibold leading-snug">{title}</h3>
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
          <h3 id="modal-title" className="text-[15px] font-semibold">{title}</h3>
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
          className={cn("rounded-md border px-3 py-1.5 text-[12.5px] font-semibold",
            value === o.v ? PILL[o.tone] + " border-current" : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const inputCls =
  "w-full rounded-md border border-bd px-3 py-2 text-[13.5px] outline-none focus:border-accent";

export const Loading = () => <div className="p-8 text-sm text-txt3">Loading…</div>;
export const Empty = ({ children }: { children: ReactNode }) => (
  <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">{children}</div>
);
