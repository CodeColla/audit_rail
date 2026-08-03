import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { Card, cn, inputCls, Loading, PageHead, Pill } from "../lib/ui";

/**
 * Certification readiness (P5-S9).
 *
 * Sumit asked whether master controls should be organised by certification type — a SOC 2
 * set, a HIPAA set, an ISO set. This screen is the answer to why not: there is **one** control
 * library, and each certification is a *lens* over it. The same control answers ISO A.8.5 and
 * SOC 2 CC6.1 at once, so evidence gathered once moves both bars here. Per-certification
 * control sets would duplicate that control, its owner and its evidence three times.
 *
 * The three states are deliberately distinct and must never be collapsed:
 *   uncovered — nothing applicable is mapped. A real gap.
 *   stale     — mapped, but nothing proves it. Answered on paper, unprovable in an audit.
 *   covered   — mapped, applicable, and the evidence has not expired.
 * A dashboard that folds "stale" into "covered" reports comfort it has not earned.
 */

type Framework = {
  id: string; code: string; name: string; version: string | null; source: string;
  clause_count: number; covered_count: number; coverage_pct: number;
};
type ReadinessClause = {
  id: string; ref: string; title: string; state: "covered" | "stale" | "uncovered";
  controls: { id: string; code: string; statement: string }[];
};
type Readiness = {
  clauses: ReadinessClause[];
  summary: { covered: number; stale: number; uncovered: number };
  total: number;
};

const STATE: Record<ReadinessClause["state"], { tone: "ok" | "warn" | "na"; label: string }> = {
  covered: { tone: "ok", label: "Covered" },
  stale: { tone: "warn", label: "No current proof" },
  uncovered: { tone: "na", label: "Not mapped" },
};

function CoverageBar({ f }: { f: Framework }) {
  return (
    <div className="mt-2">
      <div className="h-1.5 overflow-hidden rounded-full bg-canvas">
        <div className="h-full rounded-full bg-accent" style={{ width: `${f.coverage_pct}%` }} />
      </div>
      <div className="mt-1 text-[11px] text-txt3 tnum">
        {f.covered_count} of {f.clause_count} clauses mapped to an applicable control
      </div>
    </div>
  );
}

function NewFrameworkForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ code: "", name: "", version: "" });
  const [err, setErr] = useState("");
  const create = useMutation({
    mutationFn: () => api.post("/frameworks", {
      code: f.code.trim(), name: f.name.trim(), version: f.version.trim() || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["frameworks"] }); onDone(); },
    onError: (e: any) => setErr(errText(e, "Could not add that framework.")),
  });
  return (
    <Card className="mb-4">
      <div className="eyebrow mb-2">Add a framework</div>
      <div className="grid grid-cols-3 gap-3">
        <label className="text-[13px] font-medium">Code *
          <input value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })}
            placeholder="HIPAA" className={inputCls + " mt-1"} />
        </label>
        <label className="text-[13px] font-medium">Name *
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder="HIPAA Security Rule" className={inputCls + " mt-1"} />
        </label>
        <label className="text-[13px] font-medium">Version
          <input value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })}
            className={inputCls + " mt-1"} />
        </label>
      </div>
      <p className="mt-2 text-[11.5px] text-txt3">
        Add its clauses afterwards, then map them to the controls you already have — you do not
        rebuild the library for a new certification.
      </p>
      {err && <div role="alert" className="mt-2 rounded-md bg-bad-bg px-3 py-2 text-[12px] text-bad">{err}</div>}
      <div className="mt-3 flex gap-2">
        <button disabled={!f.code.trim() || !f.name.trim() || create.isPending}
          onClick={() => { setErr(""); create.mutate(); }}
          className="btn btn-primary disabled:opacity-50">
          {create.isPending ? "Adding…" : "Add framework"}
        </button>
        <button onClick={onDone} className="btn">Cancel</button>
      </div>
    </Card>
  );
}

/** One framework, drilled into: every clause and what covers it. */
function FrameworkDetail({ id }: { id: string }) {
  const [filter, setFilter] = useState<"all" | ReadinessClause["state"]>("all");
  const frameworks = useQuery({ queryKey: ["frameworks"], queryFn: () => get<Framework[]>("/frameworks") });
  const { data, isLoading } = useQuery({
    queryKey: ["readiness", id], queryFn: () => get<Readiness>(`/frameworks/${id}/readiness`) });

  if (isLoading || !data) return <Loading />;
  const me = (frameworks.data ?? []).find((f) => f.id === id);
  const rows = data.clauses.filter((c) => filter === "all" || c.state === filter);

  return (
    <>
      <PageHead eyebrow="Controls · Certification" title={me?.name ?? "Framework"}
        lead="Every clause, and which of your controls answers it. One control can answer several frameworks — that is why the evidence behind it only has to be collected once."
        action={<Link to="/frameworks" className="btn">← All frameworks</Link>} />

      <div className="mb-4 flex flex-wrap gap-2">
        {([["all", `All ${data.total}`], ["covered", `Covered ${data.summary.covered}`],
           ["stale", `No proof ${data.summary.stale}`],
           ["uncovered", `Not mapped ${data.summary.uncovered}`]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k as any)}
            className={cn("rounded-full border px-3 py-1.5 text-[12.5px] font-medium",
              filter === k ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                           : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
            {label}
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl border border-bd bg-paper">
        {rows.length === 0 && (
          <div className="p-8 text-center text-[13px] text-txt3">Nothing in this state.</div>
        )}
        {rows.map((c) => (
          <div key={c.id} className="flex items-start gap-3 border-b border-bd px-4 py-3 last:border-b-0">
            <span className="w-20 shrink-0 font-mono text-[12px] font-medium">{c.ref}</span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium">{c.title}</div>
              {c.controls.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {c.controls.map((ctl) => (
                    <Link key={ctl.id} to={`/controls/view/${ctl.id}`}
                      className="text-[11.5px] text-txt3 hover:text-accent hover:underline">
                      <span className="font-mono">{ctl.code}</span> — {ctl.statement}
                    </Link>
                  ))}
                </div>
              )}
            </div>
            <Pill tone={STATE[c.state].tone}>{STATE[c.state].label}</Pill>
          </div>
        ))}
      </div>
    </>
  );
}

export default function Frameworks() {
  const { id } = useParams();
  const can = useCan();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["frameworks"], queryFn: () => get<Framework[]>("/frameworks") });

  if (id) return <FrameworkDetail id={id} />;
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Controls · Certification" title="Frameworks"
        lead="ISO 27001, SOC 2, RBI and anything else you are audited against — measured against the one control library you already maintain."
        action={
          <div className="flex gap-2">
            <Link to="/controls" className="btn">Controls</Link>
            {can("controls", "add") && (
              <button onClick={() => setAdding(true)} className="btn btn-primary">＋ Add framework</button>
            )}
          </div>} />

      {adding && <NewFrameworkForm onDone={() => setAdding(false)} />}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((f) => (
          <Card key={f.id}>
            <div className="flex items-baseline justify-between gap-2">
              <Link to={`/frameworks/${f.id}`} className="text-[15px] font-semibold hover:underline">
                {f.name}
              </Link>
              <span className="shrink-0 text-[17px] font-semibold tnum">{f.coverage_pct}%</span>
            </div>
            <div className="text-[11.5px] text-txt3">
              <span className="font-mono">{f.code}</span>{f.version && ` · ${f.version}`}
            </div>
            <CoverageBar f={f} />
            {can("controls", "delete") && (
              <button
                onClick={() => { if (confirm(
                    `Remove ${f.name}?\n\nIts clauses and their mappings go with it. Your ` +
                    `controls, their evidence and their documents are untouched — a framework ` +
                    `is only a lens over them.`)) {
                  api.delete(`/frameworks/${f.id}`)
                    .then(() => qc.invalidateQueries({ queryKey: ["frameworks"] })); } }}
                className="mt-3 text-[11.5px] text-txt3 hover:text-bad">Remove</button>
            )}
          </Card>
        ))}
      </div>

      {rows.length === 0 && (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center">
          <h3 className="text-[15px] font-semibold">No frameworks yet</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-[13px] text-txt2">
            Add the standards you are audited against, then map their clauses to the controls
            you already have.
          </p>
        </div>
      )}
    </>
  );
}
