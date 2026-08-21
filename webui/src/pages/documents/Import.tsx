import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, errText } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { MappingReview } from "../../components/MappingReview";
import { Card, PageHead, Pill } from "../../lib/ui";

type ImportResult = { template_id: string; sections: number; questions: number; proposals: number };
type PreviewRow = { number: string; section: string; text: string };
type PreviewResult = { meta: { number_column_detected: boolean }; rows: PreviewRow[] };

export default function Import() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const can = useCan();
  // P7-S6b: "fixNumbers" is new. Most imports never see it — a checklist with a proper
  // Number column and no gaps goes straight from upload to review, exactly as before (and
  // is what e2e/30-audit-journey.spec.ts's fixture still exercises unchanged). It only
  // appears when there's something to actually decide: no Number column detected at all,
  // or individual rows missing one — which used to land silently unnumbered with no way to
  // fix it after the fact.
  const [step, setStep] = useState<"upload" | "fixNumbers" | "review">("upload");
  const [bank, setBank] = useState("");
  const [version, setVersion] = useState("");
  const [sheet, setSheet] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [qcol, setQcol] = useState("");
  const [ncol, setNcol] = useState("");
  const [scol, setScol] = useState("");
  const [hrow, setHrow] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [previewRows, setPreviewRows] = useState<PreviewRow[]>([]);
  const [numberColDetected, setNumberColDetected] = useState(true);

  const colIdx = (letter: string) => {
    let n = 0;
    for (const ch of letter.trim().toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
    return n - 1; // A -> 0
  };

  /** The `/import/preview` call, shared by the initial submit and by re-parsing after an
   *  advanced-column change — parses the file WITHOUT writing anything to the database. */
  async function preview(): Promise<PreviewResult> {
    const fd = new FormData();
    if (sheet) fd.append("sheet", sheet);
    if (advanced) {
      if (qcol) fd.append("question_col", String(colIdx(qcol)));
      if (ncol) fd.append("number_col", String(colIdx(ncol)));
      if (scol) fd.append("section_col", String(colIdx(scol)));
      if (hrow) fd.append("header_row", hrow);
    }
    fd.append("file", file!);
    const { data } = await api.post<PreviewResult>("/templates/import/preview", fd);
    return data;
  }

  /** Commits whatever row set was approved — the preview's own rows on the happy path, or
   *  the hand-fixed ones from the "fix numbers" step. Never re-parses the file: what the
   *  user saw on screen is what gets written, not a second parse that could disagree with it. */
  async function commit(rows: PreviewRow[]) {
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("bank_name", bank);
      if (version) fd.append("version_label", version);
      fd.append("rows", JSON.stringify(rows));
      fd.append("file", file!);
      const { data } = await api.post<ImportResult>("/templates/import", fd);
      setResult(data);
      setStep("review");
    } catch (e: any) {
      setErr(errText(e, "Import failed."));
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || !bank) return;
    setBusy(true); setErr("");
    try {
      const { meta, rows } = await preview();
      const allNumbered = rows.length > 0 && rows.every((r) => r.number.trim());
      setNumberColDetected(meta.number_column_detected);
      if (meta.number_column_detected && allNumbered) {
        await commit(rows);
      } else {
        setPreviewRows(rows);
        setStep("fixNumbers");
        setBusy(false);
      }
    } catch (e: any) {
      setErr(errText(e, "Import failed — check the file and column layout."));
      setBusy(false);
    }
  }

  async function createAssessment() {
    if (!result) return;
    const { data } = await api.post("/assessments",
      { template_id: result.template_id, title: `${bank} ${version || ""}`.trim() });
    qc.invalidateQueries({ queryKey: ["assessments"] });
    qc.invalidateQueries({ queryKey: ["templates"] });
    nav(`/audits/${data.id}`);
  }

  // The whole page is a write flow, so gate the page rather than each button. The API
  // re-checks audits.add on every call — this only avoids a dead-end form.
  if (!can("audits", "add")) {
    return (
      <>
        <PageHead eyebrow="Audits · Import" title="Import a bank checklist" />
        <Card className="max-w-xl text-sm text-txt2">
          You do not have permission to import checklists. Ask an administrator for the
          <span className="font-medium"> Audits · add</span> permission.
        </Card>
      </>
    );
  }

  if (step === "upload") {
    return (
      <>
        <PageHead eyebrow="Audits · Import" title="Import a bank checklist"
          lead="Drop the bank's XLSX. We detect the columns, create the template, and propose a mapping onto your standard controls." />
        <Card className="max-w-xl">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm font-medium">Bank name
                <input value={bank} onChange={(e) => setBank(e.target.value)} required placeholder="ICICI"
                  className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-body outline-none focus:border-accent" />
              </label>
              <label className="text-sm font-medium">Version <span className="text-txt3">(optional)</span>
                <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="v1.2"
                  className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-body outline-none focus:border-accent" />
              </label>
            </div>
            <label className="text-sm font-medium">Sheet name <span className="text-txt3">(optional — first sheet by default)</span>
              <input value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder="Sheet1"
                className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-body outline-none focus:border-accent" />
            </label>
            <label className="mt-1 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-hair bg-canvas px-4 py-8 text-center">
              <span className="text-sm font-medium">{file ? file.name : "Choose an XLSX / CSV checklist"}</span>
              <span className="text-caption text-txt3">columns are auto-detected; you confirm the mappings next</span>
              <input type="file" accept=".xlsx,.csv" className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button type="button" onClick={() => setAdvanced((a) => !a)}
              className="self-start text-label font-medium text-txt2 hover:text-ink">
              {advanced ? "▾" : "▸"} Column overrides (for messy sheets)
            </button>
            {advanced && (
              <div className="grid grid-cols-4 gap-2 rounded-md border border-bd bg-canvas p-3">
                {[["Question col", qcol, setQcol, "G"], ["Number col", ncol, setNcol, "B"],
                  ["Section col", scol, setScol, "D"], ["Header row", hrow, setHrow, "5"]].map(
                  ([label, val, set, ph]: any) => (
                    <label key={label} className="text-caption font-medium text-txt2">{label}
                      <input value={val} onChange={(e) => set(e.target.value)} placeholder={ph}
                        className="mt-1 w-full rounded border border-bd px-2 py-1.5 text-sm outline-none focus:border-accent" />
                    </label>
                  ))}
                <p className="col-span-4 text-caption text-txt3">Use column letters (A, B, G…). Leave blank to auto-detect.</p>
              </div>
            )}
            {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
            <button disabled={busy || !file || !bank} className="btn btn-primary justify-center disabled:opacity-50">
              {busy ? "Reading…" : "Import & propose mappings"}
            </button>
          </form>
        </Card>
      </>
    );
  }

  if (step === "fixNumbers") {
    const missing = previewRows.filter((r) => !r.number.trim()).length;
    const setRowNumber = (i: number, value: string) =>
      setPreviewRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, number: value } : r)));

    return (
      <>
        <PageHead eyebrow="Audits · Import" title="Check the question numbers"
          lead="Every question needs a number before this can be imported — that's what lets you find and edit a specific audit point later." />
        <Card className="max-w-3xl">
          {!numberColDetected && (
            <div className="mb-3 rounded-md bg-warn-bg px-3 py-2 text-label text-warn">
              No Number column was detected in this file at all. Fill one in below, or go back
              and set it explicitly under "Column overrides".
            </div>
          )}
          {numberColDetected && missing > 0 && (
            <div className="mb-3 rounded-md bg-warn-bg px-3 py-2 text-label text-warn">
              {missing} of {previewRows.length} question{previewRows.length === 1 ? "" : "s"} came
              in with no number. Fill in the missing ones below to continue.
            </div>
          )}
          <div className="max-h-[52vh] overflow-y-auto rounded-md border border-bd">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-canvas text-caption text-txt3">
                <tr>
                  <th className="px-3 py-2 font-medium">No.</th>
                  <th className="px-3 py-2 font-medium">Section</th>
                  <th className="px-3 py-2 font-medium">Question</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.map((r, i) => (
                  <tr key={i} className="border-t border-bd">
                    <td className="px-3 py-1.5">
                      <input value={r.number} onChange={(e) => setRowNumber(i, e.target.value)}
                        aria-label={`Number for row ${i + 1}`}
                        className={
                          "w-20 rounded border px-1.5 py-1 font-mono text-sm outline-none focus:border-accent " +
                          (r.number.trim() ? "border-bd" : "border-bad bg-bad-bg")} />
                    </td>
                    <td className="px-3 py-1.5 text-txt3">{r.section || "—"}</td>
                    <td className="px-3 py-1.5">{r.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {err && <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
          <div className="mt-3 flex items-center gap-2">
            <button type="button" onClick={() => setStep("upload")} className="btn">← Back</button>
            <button disabled={busy || missing > 0} onClick={() => commit(previewRows)}
              className="btn btn-primary disabled:opacity-50">
              {busy ? "Importing…" : `Import ${previewRows.length} questions`}
            </button>
          </div>
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHead eyebrow="Audits · Import" title={`${bank} — review mappings`}
        lead="Each bank question is proposed onto one of your standard controls. Confirm the good ones; reject or re-map the rest."
        action={
          <button onClick={createAssessment} className="btn btn-primary">Create assessment →</button>
        } />
      {/* What THIS import produced. Deliberately not inside MappingReview: those counts come
          from the import result and mean nothing on the standalone review screen, where the
          checklist may have been imported months ago. Dropping them in the S10 refactor lost
          the only confirmation that the file parsed into the number of questions expected —
          caught by 30-audit-journey, which asserts on "6 questions". */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Pill tone="ok">{result?.questions} questions</Pill>
        <Pill tone="info">{result?.sections} sections</Pill>
      </div>
      {err && <div className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
      {/* P5-S10: the same component the standalone review screen uses. It used to be written
          inline here, which is precisely why the review was unreachable once you left the
          wizard — see MappingReview's own note. */}
      <MappingReview templateId={result!.template_id} />
    </>
  );
}
