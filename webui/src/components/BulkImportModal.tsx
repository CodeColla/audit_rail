import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile, errText, get } from "../lib/api";
import { inputCls, Modal } from "../lib/ui";
import { MAX_UPLOAD_MB } from "../lib/evidence";

/**
 * Bulk import for the registers (P5-S5).
 *
 * Three deliberate choices, all of them about not being confidently wrong:
 *
 *  1. **The column list comes from the server** (`/import/{register}/columns`), the same
 *     structure that generates the template and drives the row builder. Duplicating it here
 *     is how a UI ends up offering a column the importer ignores.
 *  2. **Mapping is explicit.** We pre-select a target only on an EXACT header match; anything
 *     else the user picks. Fuzzy-matching "Owner Name" onto `owner` because it looks close is
 *     how a bulk import quietly fills the wrong column, and it reports success while doing it.
 *  3. **Row errors are shown in full, not counted.** "12 rows failed" is useless; the row
 *     number, the name and the reason are what let someone fix the file and retry.
 */

type Col = { key: string; label: string; help: string; required: boolean };
type RowError = { row: number; name: string | null; error: string };
type Result = { created: number; failed: number; errors: RowError[] };

export function BulkImportModal({ register, basePath, templateName, onClose, onImported }: {
  register: string;
  /** Where the three import endpoints live. Defaults to the registers' own
   *  `/import/{register}`; framework clauses pass `/frameworks/{id}/import` (P5-S10). The
   *  contract is identical either way — `{base}/columns`, `{base}/template.xlsx`, `POST
   *  {base}` — so one modal serves both rather than a near-copy drifting out of step. */
  basePath?: string;
  templateName?: string;
  onClose: () => void;
  onImported?: () => void;
}) {
  const base = basePath ?? `/import/${register}`;
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<Result | null>(null);

  const spec = useQuery({
    queryKey: ["import-columns", base],
    queryFn: () => get<{ noun: string; columns: Col[] }>(`${base}/columns`),
  });
  const columns = spec.data?.columns ?? [];

  // Read the header row in the BROWSER so the mapping table can be shown before anything is
  // uploaded. SheetJS is already a dependency (evidence previews) and is loaded dynamically,
  // so this costs nothing for users who never import.
  async function readHeaders(f: File) {
    setErr(""); setResult(null);
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setErr(`That file is over the ${MAX_UPLOAD_MB} MB limit.`);
      return;
    }
    try {
      const XLSX = await import("xlsx");
      const wb = XLSX.read(await f.arrayBuffer(), { type: "array" });
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, blankrows: false });
      const head = (rows[0] ?? []).map((h) => String(h ?? "").trim()).filter(Boolean);
      if (!head.length) { setErr("That file has no header row."); return; }
      setFile(f);
      setHeaders(head);
      // Exact matches only — see the note above on why nothing fuzzier belongs here.
      const auto: Record<string, string> = {};
      for (const c of columns) {
        const hit = head.find((h) => h.toLowerCase() === c.label.toLowerCase());
        if (hit) auto[c.key] = hit;
      }
      setMapping(auto);
    } catch {
      setErr("Could not read that file — is it a .xlsx or .csv?");
    }
  }

  // Re-run the auto-mapping once the column spec arrives, in case the file was picked first.
  useEffect(() => {
    if (!headers.length || !columns.length || Object.keys(mapping).length) return;
    const auto: Record<string, string> = {};
    for (const c of columns) {
      const hit = headers.find((h) => h.toLowerCase() === c.label.toLowerCase());
      if (hit) auto[c.key] = hit;
    }
    if (Object.keys(auto).length) setMapping(auto);
  }, [headers, columns.length]);   // eslint-disable-line react-hooks/exhaustive-deps

  const missingRequired = columns.filter((c) => c.required && !mapping[c.key]);

  async function submit() {
    if (!file) return;
    setBusy(true); setErr(""); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("mapping", JSON.stringify(mapping));
      const r = await api.post(base, fd);
      setResult(r.data as Result);
      if ((r.data as Result).created > 0) {
        qc.invalidateQueries();      // the register, its counts and the dashboard all move
        onImported?.();
      }
    } catch (e: any) {
      setErr(errText(e, "Could not import that file."));
    } finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title={`Import ${spec.data?.noun ?? register}`} size="lg">
      <p className="mb-3 text-[12.5px] text-txt2">
        Upload a <b>.xlsx</b> or <b>.csv</b>. Owners and vendors are matched by name — or by
        email address, which is the only way to be unambiguous when two people share a name.
      </p>

      <button onClick={() => downloadFile(`${base}/template.xlsx`,
                                          templateName ?? `${register}-import-template.xlsx`)}
        className="btn mb-3 py-1.5 text-[12.5px]">⬇ Download the template</button>

      <label className="btn w-full cursor-pointer justify-center py-1.5">
        {file ? `Selected: ${file.name}` : "Choose a file"}
        <input type="file" accept=".xlsx,.xls,.csv" className="hidden" disabled={busy}
          onChange={(e) => {
            // Snapshot before the reset — `files` is a live FileList (the P5-S1 race).
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void readHeaders(f);
          }} />
      </label>

      {headers.length > 0 && (
        <div className="mt-4">
          <div className="eyebrow mb-2">Match your columns</div>
          <div className="max-h-64 divide-y divide-bd overflow-y-auto rounded-md border border-bd">
            {columns.map((c) => (
              <div key={c.key} className="flex items-center gap-3 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-[12.5px] font-medium">
                    {c.label}{c.required && <span className="text-bad"> *</span>}
                  </div>
                  {c.help && <div className="text-[11px] text-txt3">{c.help}</div>}
                </div>
                <select
                  value={mapping[c.key] ?? ""}
                  onChange={(e) => setMapping((m) => {
                    const next = { ...m };
                    if (e.target.value) next[c.key] = e.target.value; else delete next[c.key];
                    return next;
                  })}
                  className={inputCls + " max-w-[46%] shrink-0"}>
                  <option value="">— not in my file —</option>
                  {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
            ))}
          </div>
          {missingRequired.length > 0 && (
            <p className="mt-2 text-[12px] text-warn">
              Still need a column for {missingRequired.map((c) => c.label).join(", ")}.
            </p>
          )}
        </div>
      )}

      {err && <div role="alert" className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}

      {result && (
        <div className="mt-4">
          <div className={`rounded-md px-3 py-2 text-[12.5px] ${
            result.failed === 0 ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
            Imported <b>{result.created}</b>
            {result.failed > 0 && <> · <b>{result.failed}</b> row{result.failed === 1 ? "" : "s"} could not be imported</>}
          </div>
          {result.errors.length > 0 && (
            // Every failure, with its row number — a count alone gives nobody a way to fix
            // the file. Rows that DID import are already saved; only these need another go.
            <div className="mt-2 max-h-48 divide-y divide-bd overflow-y-auto rounded-md border border-bd">
              {result.errors.map((e, i) => (
                <div key={i} className="px-3 py-1.5 text-[12px]">
                  <span className="font-mono text-txt3">row {e.row}</span>
                  {e.name && <span className="ml-2 font-medium">{e.name}</span>}
                  <div className="text-bad">{e.error}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        {/* "Done", not "Close": the Modal chrome already has a ✕ whose accessible name is
            "Close", and two identically-named controls are ambiguous for a screen reader
            (and for anything trying to click one of them). */}
        <button onClick={onClose} className="btn">{result ? "Done" : "Cancel"}</button>
        <button onClick={submit}
          disabled={!file || busy || missingRequired.length > 0}
          className="btn btn-primary disabled:opacity-50">
          {busy ? "Importing…" : result ? "Import again" : "Import"}
        </button>
      </div>
    </Modal>
  );
}
