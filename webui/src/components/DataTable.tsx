import { ReactNode, useEffect, useMemo, useState } from "react";
import { cn, inputCls, Td } from "../lib/ui";
import { useDebounced } from "../lib/useDebounced";

export type Column<T> = {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  className?: string;
  /** Omit for an unsortable column (e.g. an actions cell). Returning `null`/`undefined`
   * always sorts that row last, in either direction. */
  sortValue?: (row: T) => string | number | null | undefined;
};

/**
 * The shared list toolkit — search, sortable columns, row selection, and bulk delete —
 * over the app's existing `Table`/`Td` styling. Built once in P4-S1, proven on Evidence,
 * rolled out to the other registers in P4-S4.
 *
 * Search is one-directional: `DataTable` owns the input's live value AND the debounce
 * entirely, and only ever calls `onSearch` with the settled, debounced query — there is no
 * `search` value fed back in from the caller. (An earlier draft took a controlled
 * `search`/`onSearchChange` pair and debounced on BOTH sides, which double-delayed every
 * query and re-seeded DataTable's internal input only on mount — a classic derived-state
 * bug. One direction, one debounce.) The caller's job is just: hold whatever `onSearch`
 * last reported, and use it in a `useQuery` — search here is server-side (`?q=`, already
 * supported by seven endpoints per the P5 audit), so the caller owns the actual fetch.
 *
 * Sort is genuinely client-side, over whatever page of rows the caller already fetched —
 * correct at this app's real scale (hundreds of rows), and it costs no backend work.
 *
 * Bulk delete is N sequential single-DELETE calls, not a bulk endpoint — this API has no
 * partial-success envelope precedent, so per-row outcomes are reported the same way P4-S8's
 * batch evidence upload already does.
 */
const DEFAULT_BULK_COPY = {
  button: "Delete selected", busy: "Deleting…",
  confirm: (label: string) => `Delete ${label}? This cannot be undone.`,
  errorPrefix: "could not be deleted",
};

export function DataTable<T>({
  rows, getId, columns,
  onSearch, searchPlaceholder = "Search…",
  onDeleteOne, canDelete = false, bulkActionCopy,
  onRowClick,
  emptyMessage = "Nothing here yet.",
  noMatchMessage,
}: {
  rows: T[];
  getId: (row: T) => string;
  columns: Column<T>[];
  /** Present -> renders a search box. Called with the DEBOUNCED query on every settle,
   * including `""` when cleared. Omit entirely for a page with no search. */
  onSearch?: (debouncedQuery: string) => void;
  searchPlaceholder?: string;
  /** Run a bulk row action, one id at a time; throw (or reject) on failure with a message.
   * Required for the bulk-action bar and the per-row inline affordance. Despite the name,
   * this need not be destructive — see `bulkActionCopy`. */
  onDeleteOne?: (id: string) => Promise<void>;
  canDelete?: boolean;
  /** Override the bulk-action button/confirm/error wording — e.g. Documents' bulk action
   * archives (reversible, existing Restore button) rather than deletes, so "Delete selected"
   * and "cannot be undone" would both be false. Defaults to delete wording. */
  bulkActionCopy?: { button: string; busy: string; confirm: (label: string) => string; errorPrefix: string };
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
  noMatchMessage?: string;
}) {
  const copy = bulkActionCopy ?? DEFAULT_BULK_COPY;
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  useEffect(() => { onSearch?.(dq); }, [dq]); // eslint-disable-line react-hooks/exhaustive-deps

  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErrors, setBulkErrors] = useState<string[]>([]);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col?.sortValue) return rows;
    const withKeys = rows.map((r) => ({ r, v: col.sortValue!(r) }));
    withKeys.sort((a, b) => {
      if (a.v == null && b.v == null) return 0;
      if (a.v == null) return 1;      // nulls sort last regardless of direction
      if (b.v == null) return -1;
      if (a.v < b.v) return sortDir === "asc" ? -1 : 1;
      if (a.v > b.v) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return withKeys.map((x) => x.r);
  }, [rows, sortKey, sortDir, columns]);

  const toggleSort = (key: string) => {
    if (sortKey !== key) { setSortKey(key); setSortDir("asc"); return; }
    if (sortDir === "asc") { setSortDir("desc"); return; }
    setSortKey(null); // third click clears — back to the server's own order
  };

  const allIds = sorted.map(getId);
  const allSelected = allIds.length > 0 && allIds.every((id) => selected.has(id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(allIds));
  const toggleOne = (id: string) => setSelected((s) => {
    const next = new Set(s);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  async function bulkDelete() {
    if (!onDeleteOne || selected.size === 0) return;
    const ids = [...selected];
    const label = ids.length === 1 ? "this item" : `these ${ids.length} items`;
    if (!confirm(copy.confirm(label))) return;
    setBulkBusy(true);
    const errors: string[] = [];
    for (const id of ids) {
      try {
        await onDeleteOne(id);
        setSelected((s) => { const n = new Set(s); n.delete(id); return n; });
      } catch (e: any) {
        errors.push(e?.response?.data?.detail ?? e?.message ?? `Could not process ${id}.`);
      }
    }
    setBulkBusy(false);
    setBulkErrors(errors);
  }

  return (
    <div className="flex flex-col gap-3">
      {onSearch && (
        <input value={term} onChange={(e) => setTerm(e.target.value)}
          placeholder={searchPlaceholder} className={inputCls + " max-w-sm"} />
      )}

      {selected.size > 0 && (
        <div className="flex items-center gap-3 rounded-md border border-bd bg-canvas px-3 py-2 text-[12.5px]">
          <span className="font-medium">{selected.size} selected</span>
          {canDelete && onDeleteOne && (
            <button onClick={bulkDelete} disabled={bulkBusy}
              className="text-bad hover:underline disabled:opacity-50">
              {bulkBusy ? copy.busy : copy.button}
            </button>
          )}
          <button onClick={() => setSelected(new Set())} className="text-txt3 hover:underline">Clear</button>
        </div>
      )}
      {bulkErrors.length > 0 && (
        <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">
          {bulkErrors.length} {copy.errorPrefix}: {bulkErrors.join("; ")}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          {dq && noMatchMessage ? noMatchMessage : emptyMessage}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-bd bg-paper">
          <table className="w-full text-[13px]">
            <thead>
              <tr>
                {onDeleteOne && canDelete && (
                  <th className="w-8 border-b border-bd bg-canvas px-3">
                    <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all" />
                  </th>
                )}
                {columns.map((col) => (
                  <th key={col.key}
                    className="border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.09em] text-txt3">
                    {col.sortValue ? (
                      <button onClick={() => toggleSort(col.key)}
                        className="flex items-center gap-1 hover:text-ink">
                        {col.label}
                        <span className="text-[9px]">
                          {sortKey === col.key ? (sortDir === "asc" ? "▲" : "▼") : "⇅"}
                        </span>
                      </button>
                    ) : col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const id = getId(row);
                return (
                  <tr key={id} className={cn("hover:bg-canvas", onRowClick && "cursor-pointer")}
                    onClick={() => onRowClick?.(row)}>
                    {onDeleteOne && canDelete && (
                      <td className="w-8 border-b border-bd px-3.5 py-3 align-middle"
                        onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={selected.has(id)} onChange={() => toggleOne(id)}
                          aria-label="Select row" />
                      </td>
                    )}
                    {columns.map((col) => (
                      <Td key={col.key} className={col.className}>{col.render(row)}</Td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
