import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Bar, Card, cn, PageHead, Pill } from "../lib/ui";

type Proposal = {
  question_id: string; number: string | null; text: string;
  control_id: string | null; code: string | null; statement: string | null;
  confidence: number | null; status: string;
};
type ImportResult = { template_id: string; sections: number; questions: number; proposals: number };

export default function Import() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState<"upload" | "review">("upload");
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
  const [proposals, setProposals] = useState<Proposal[]>([]);

  async function loadProposals(tpl: string) {
    const { data } = await api.get<Proposal[]>(`/templates/${tpl}/proposals`);
    setProposals(data);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || !bank) return;
    setBusy(true); setErr("");
    try {
      const colIdx = (letter: string) => {
        let n = 0;
        for (const ch of letter.trim().toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
        return n - 1; // A -> 0
      };
      const fd = new FormData();
      fd.append("bank_name", bank);
      if (version) fd.append("version_label", version);
      if (sheet) fd.append("sheet", sheet);
      if (advanced) {
        if (qcol) fd.append("question_col", String(colIdx(qcol)));
        if (ncol) fd.append("number_col", String(colIdx(ncol)));
        if (scol) fd.append("section_col", String(colIdx(scol)));
        if (hrow) fd.append("header_row", hrow);
      }
      fd.append("file", file);
      const { data } = await api.post<ImportResult>("/templates/import", fd);
      setResult(data);
      await loadProposals(data.template_id);
      setStep("review");
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Import failed — check the file and column layout.");
    } finally {
      setBusy(false);
    }
  }

  async function decide(question_id: string, action: "confirm" | "reject") {
    if (!result) return;
    await api.post(`/templates/${result.template_id}/proposals/confirm`,
      { decisions: [{ question_id, action }] });
    setProposals((p) => p.map((x) => x.question_id === question_id
      ? { ...x, status: action === "confirm" ? "confirmed" : "rejected" } : x));
  }

  async function confirmAll() {
    if (!result) return;
    await api.post(`/templates/${result.template_id}/proposals/confirm`,
      { confirm_high_confidence: 0.5 });
    await loadProposals(result.template_id);
  }

  async function createAssessment() {
    if (!result) return;
    const { data } = await api.post("/assessments",
      { template_id: result.template_id, title: `${bank} ${version || ""}`.trim() });
    qc.invalidateQueries({ queryKey: ["assessments"] });
    qc.invalidateQueries({ queryKey: ["templates"] });
    nav(`/audits/${data.id}`);
  }

  if (step === "upload") {
    return (
      <>
        <PageHead eyebrow="Audits · Import" title="Import a bank checklist"
          lead="Drop the bank's XLSX. We detect the columns, create the template, and propose a mapping onto your standard controls." />
        <Card className="max-w-xl">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-[13px] font-medium">Bank name
                <input value={bank} onChange={(e) => setBank(e.target.value)} required placeholder="ICICI"
                  className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent" />
              </label>
              <label className="text-[13px] font-medium">Version <span className="text-txt3">(optional)</span>
                <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="v1.2"
                  className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent" />
              </label>
            </div>
            <label className="text-[13px] font-medium">Sheet name <span className="text-txt3">(optional — first sheet by default)</span>
              <input value={sheet} onChange={(e) => setSheet(e.target.value)} placeholder="Sheet1"
                className="mt-1 w-full rounded-md border border-bd px-3 py-2 text-[14px] outline-none focus:border-accent" />
            </label>
            <label className="mt-1 flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-hair bg-canvas px-4 py-8 text-center">
              <span className="text-[13px] font-medium">{file ? file.name : "Choose an XLSX / CSV checklist"}</span>
              <span className="text-[11.5px] text-txt3">columns are auto-detected; you confirm the mappings next</span>
              <input type="file" accept=".xlsx,.csv" className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button type="button" onClick={() => setAdvanced((a) => !a)}
              className="self-start text-[12px] font-medium text-txt2 hover:text-ink">
              {advanced ? "▾" : "▸"} Column overrides (for messy sheets)
            </button>
            {advanced && (
              <div className="grid grid-cols-4 gap-2 rounded-md border border-bd bg-canvas p-3">
                {[["Question col", qcol, setQcol, "G"], ["Number col", ncol, setNcol, "B"],
                  ["Section col", scol, setScol, "D"], ["Header row", hrow, setHrow, "5"]].map(
                  ([label, val, set, ph]: any) => (
                    <label key={label} className="text-[11.5px] font-medium text-txt2">{label}
                      <input value={val} onChange={(e) => set(e.target.value)} placeholder={ph}
                        className="mt-1 w-full rounded border border-bd px-2 py-1.5 text-[13px] outline-none focus:border-accent" />
                    </label>
                  ))}
                <p className="col-span-4 text-[11px] text-txt3">Use column letters (A, B, G…). Leave blank to auto-detect.</p>
              </div>
            )}
            {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
            <button disabled={busy || !file || !bank} className="btn btn-primary justify-center disabled:opacity-50">
              {busy ? "Importing…" : "Import & propose mappings"}
            </button>
          </form>
        </Card>
      </>
    );
  }

  const confirmed = proposals.filter((p) => p.status === "confirmed").length;
  return (
    <>
      <PageHead eyebrow="Audits · Import" title={`${bank} — review mappings`}
        lead="Each bank question is proposed onto one of your standard controls. Confirm the good ones; reject or re-map the rest." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Pill tone="ok">{result?.questions} questions</Pill>
        <Pill tone="info">{result?.sections} sections</Pill>
        <Pill tone="warn">{confirmed} confirmed</Pill>
        <div className="ml-auto flex gap-2">
          <button onClick={confirmAll} className="btn">Confirm all ≥ 50%</button>
          <button onClick={createAssessment} className="btn btn-primary">Create assessment →</button>
        </div>
      </div>

      <div className="max-h-[65vh] overflow-y-auto rounded-xl border border-bd bg-paper">
        <table className="w-full text-[13px]">
          <thead className="sticky top-0">
            <tr>
              {["#", "Bank question", "Proposed standard control", "Confidence", ""].map((h, i) => (
                <th key={i} className="border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-txt3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {proposals.map((p) => {
              const c = Math.round((p.confidence ?? 0) * 100);
              return (
                <tr key={p.question_id} className={cn("border-b border-bd", p.status === "rejected" && "opacity-40")}>
                  <td className="px-3.5 py-2.5 font-mono text-txt3">#{p.number}</td>
                  <td className="px-3.5 py-2.5">{p.text}</td>
                  <td className="px-3.5 py-2.5">
                    {p.code ? <><span className="font-mono font-semibold text-accent">{p.code}</span>
                      <span className="ml-2 text-txt3">{p.statement}</span></> : <span className="text-txt3">no match</span>}
                  </td>
                  <td className="w-32 px-3.5 py-2.5">
                    <Bar pct={c} muted={c < 30} /><div className="mt-1 text-[11px] text-txt3 tnum">{c}%</div>
                  </td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">
                    {p.status === "suggested" ? (
                      <div className="flex gap-1.5">
                        <button onClick={() => decide(p.question_id, "confirm")}
                          className="grid h-7 w-7 place-items-center rounded-md border border-ok/40 text-ok hover:bg-ok-bg" title="Confirm">✓</button>
                        <button onClick={() => decide(p.question_id, "reject")}
                          className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad" title="Reject">✕</button>
                      </div>
                    ) : <Pill tone={p.status === "confirmed" ? "ok" : "na"}>{p.status}</Pill>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
