import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { get } from "../lib/api";
import { useDebounced } from "../lib/useDebounced";
import { cn } from "../lib/ui";

/**
 * The header search — wired for the first time in P5-S6.
 *
 * It has been a bare `<input placeholder="Search…">` with no value, no handler and no query
 * since it was drawn: purely decorative chrome that looked like a feature. That was the first
 * item in Sumit's Phase 5 feedback.
 *
 * There is no server-side global search endpoint, and inventing one (a UNION across eight
 * tables, or a tsvector index) is a genuine piece of backend design that does not belong in
 * the last sprint of a UI phase. Instead this fans out to the `?q=` each register **already
 * supports** — the same parameter the S4 rollout wired into the list screens — and groups the
 * results. Concretely: six small indexed LIKE queries, only while the box has focus and a
 * settled query, against endpoints that are already the app's read path.
 *
 * If this becomes slow enough to matter, the fix is one real search endpoint behind the same
 * component, not a rewrite here.
 */

type Hit = { id: string; label: string; sub?: string | null };
type Group = { key: string; label: string; path: string; hits: Hit[] };

/** Each source: the endpoint, how to title a row, and where clicking goes. */
const SOURCES = [
  { key: "documents", label: "Documents", url: "/documents", to: (id: string) => `/documents/${id}`,
    title: (r: any) => r.title, sub: (r: any) => r.document_type?.toLowerCase() },
  { key: "evidence", label: "Evidence", url: "/evidence", to: (id: string) => `/evidence/view/${id}`,
    title: (r: any) => r.title, sub: (r: any) => r.evidence_type },
  { key: "risks", label: "Risks", url: "/risks", to: (id: string) => `/risks/view/${id}`,
    title: (r: any) => r.title, sub: (r: any) => r.reference },
  { key: "assets", label: "Assets", url: "/assets", to: (id: string) => `/assets/view/${id}`,
    title: (r: any) => r.name, sub: (r: any) => r.asset_type?.toLowerCase() },
  { key: "third-parties", label: "Third parties", url: "/third-parties",
    to: (id: string) => `/third-parties/view/${id}`,
    title: (r: any) => r.name, sub: (r: any) => r.category },
  { key: "people", label: "People", url: "/people", to: () => `/people`,
    title: (r: any) => r.full_name, sub: (r: any) => r.department },
] as const;

const PER_SOURCE = 5;

export function GlobalSearch() {
  const nav = useNavigate();
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const dq = useDebounced(term);
  const box = useRef<HTMLDivElement>(null);

  // Close on an outside click. Without this the panel survives navigation and hangs over
  // whatever page you just opened.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const results = useQuery({
    queryKey: ["global-search", dq],
    enabled: dq.trim().length >= 2,       // one character matches most of the database
    queryFn: async (): Promise<Group[]> => {
      const q = encodeURIComponent(dq.trim());
      const settled = await Promise.allSettled(
        SOURCES.map((s) => get<any[]>(`${s.url}?q=${q}`)));
      return SOURCES.map((s, i) => {
        const r = settled[i];
        // A source the caller lacks permission for 403s. That must narrow the results, never
        // blank the whole panel — hence allSettled and a per-source skip.
        const rows = r.status === "fulfilled" ? r.value : [];
        return {
          key: s.key, label: s.label, path: s.url,
          hits: rows.slice(0, PER_SOURCE).map((row) => ({
            id: row.id, label: s.title(row), sub: s.sub(row),
          })),
        };
      }).filter((g) => g.hits.length > 0);
    },
  });

  const groups = results.data ?? [];
  const total = groups.reduce((n, g) => n + g.hits.length, 0);
  const showPanel = open && dq.trim().length >= 2;

  function go(to: string) {
    setOpen(false);
    setTerm("");
    nav(to);
  }

  return (
    <div ref={box} className="relative ml-auto">
      <div className="flex items-center gap-2 rounded-full border border-bd bg-paper px-3 py-1.5 text-txt3">
        <Search size={15} />
        <input
          value={term}
          onChange={(e) => { setTerm(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => { if (e.key === "Escape") { setOpen(false); (e.target as HTMLInputElement).blur(); } }}
          placeholder="Search…"
          aria-label="Search everything"
          className="w-40 bg-transparent text-[13px] text-ink outline-none placeholder:text-txt3 focus:w-64" />
      </div>

      {showPanel && (
        <div className="absolute right-0 top-full z-40 mt-1 max-h-[70vh] w-[26rem] overflow-y-auto rounded-md border border-bd bg-paper shadow-drawer">
          {results.isPending ? (
            <div className="px-3 py-2.5 text-[12.5px] text-txt3">Searching…</div>
          ) : total === 0 ? (
            <div className="px-3 py-2.5 text-[12.5px] text-txt3">Nothing matches “{dq}”.</div>
          ) : (
            groups.map((g) => (
              <div key={g.key} className="border-b border-bd last:border-b-0">
                <div className="bg-canvas px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.09em] text-txt3">
                  {g.label}
                </div>
                {g.hits.map((h) => {
                  const src = SOURCES.find((s) => s.key === g.key)!;
                  return (
                    <button key={h.id} onClick={() => go(src.to(h.id))}
                      className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-canvas">
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">{h.label}</span>
                      {h.sub && <span className="shrink-0 text-[11px] capitalize text-txt3">{h.sub}</span>}
                    </button>
                  );
                })}
                {/* Each source is capped, so say so and offer the full filtered list rather
                    than quietly implying these are all the matches. */}
                <button onClick={() => go(`${g.path}`)}
                  className={cn("w-full px-3 py-1 text-left text-[11px] text-txt3 hover:text-accent",
                    g.hits.length < PER_SOURCE && "hidden")}>
                  See all {g.label.toLowerCase()} →
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
