import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";

export type ControlOption = { id: string; code: string; statement: string };

/**
 * A searchable list over the control library (P4-S6) — shared by the post-import proposal
 * review (Import.tsx) and the mid-assessment remap action (Workspace.tsx), so a control
 * search only has one implementation.
 *
 * P4-S8 moved the filter to the server (`?q=`), which means the query key MUST carry the
 * term. Left as a bare ["controls-lite"], the first fetch's results would be cached and
 * every later keystroke would render them unchanged — and worse, whatever this picker last
 * searched for would become the whole list for Registers.tsx's obligation picker, which
 * reads the same key.
 */
export function ControlPicker({ onPick, onClose }: {
  onPick: (c: ControlOption) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const controls = useQuery({
    queryKey: ["controls-lite", dq],
    queryFn: () => get<ControlOption[]>(
      `/library/controls?${new URLSearchParams(dq ? { q: dq } : {})}`),
    placeholderData: keepPreviousData,   // no empty flash between keystrokes
  });
  const filtered = controls.data ?? [];
  return (
    <div className="w-full max-w-[520px] rounded-md border border-bd bg-canvas">
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search controls…"
        className="w-full border-b border-bd bg-paper px-3 py-2 text-label outline-none" />
      <div className="max-h-56 overflow-y-auto">
        {filtered.map((c) => (
          <button key={c.id} onClick={() => onPick(c)}
            className="flex w-full flex-col items-start px-3 py-2 text-left text-label hover:bg-paper">
            <span className="font-mono font-semibold text-accent">{c.code}</span>
            <span className="text-txt3">{c.statement}</span>
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="px-3 py-2 text-label text-txt3">
            {controls.isFetching ? "Searching…" : "No match."}</div>)}
      </div>
      <button onClick={onClose} className="w-full border-t border-bd px-3 py-1.5 text-caption text-txt3 hover:bg-paper">Cancel</button>
    </div>
  );
}
