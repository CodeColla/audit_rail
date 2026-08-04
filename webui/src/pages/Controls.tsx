import { useMemo, useState } from "react";
import { Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { OwnerSelect } from "./Registers";
import { Loading, Modal, PageHead, Pill, TH, Table, Td, cn, inputCls } from "../lib/ui";

type Domain = { id: string; code: string; name: string; control_count: number };
type Control = {
  id: string; code: string; statement: string; lifecycle: string; recurrence_months: number | null;
  applicability: string; reactivation_trigger: string | null; stock_response: string | null;
  domain_code: string; domain_name: string; mapped_count: number;
};
type Xwalk = {
  columns: { id: string; bank_name: string; version_label: string }[];
  rows: { control_id: string; code: string; statement: string; domain_code: string; cells: Record<string, string[]> }[];
};

const LIFECYCLE = ["one_time", "recurring", "per_audit"] as const;

const lifecycleLabel = (c: Control) =>
  c.lifecycle === "recurring" ? `Recurring · ${c.recurrence_months}mo`
  : c.lifecycle === "one_time" ? "One-time" : "Per audit";

// ─────────────────────────────────────────────────────── add control
function AddControlModal({ domains, onClose }: { domains: Domain[]; onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState({
    domain_id: domains[0]?.id ?? "", code: "", statement: "", lifecycle: "per_audit",
    recurrence_months: "", applicability: "applicable", na_justification: "",
    reactivation_trigger: "", owner_person_id: "",
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const create = useMutation({
    mutationFn: () => api.post("/library/controls", {
      ...f, recurrence_months: f.recurrence_months ? +f.recurrence_months : null }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["controls"] });
      qc.invalidateQueries({ queryKey: ["domains"] });
      nav(`/controls/view/${r.data.id}`);
    },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });

  const disabled = create.isPending || !f.domain_id || !f.code.trim() || !f.statement.trim()
    || (f.lifecycle === "recurring" && !f.recurrence_months)
    || (f.applicability === "not_applicable" && !f.na_justification.trim());

  return (
    <Modal open onClose={onClose} title="New control" size="lg">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Domain
            <select value={f.domain_id} onChange={set("domain_id")} className={inputCls + " mt-1"}>
              {domains.length === 0 && <option value="">Loading…</option>}
              {domains.map((d) => <option key={d.id} value={d.id}>{d.code} — {d.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium">Reference code *
            <input value={f.code} onChange={set("code")} className={inputCls + " mt-1"}
              placeholder="AM 4.a" /></label>
        </div>
        <label className="text-sm font-medium">Statement *
          <textarea value={f.statement} onChange={set("statement")}
            className={cn(inputCls, "mt-1 min-h-[64px]")}
            placeholder="Strong password policy is enforced for all systems." /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Lifecycle
            <select value={f.lifecycle} onChange={set("lifecycle")} className={inputCls + " mt-1"}>
              {LIFECYCLE.map((l) => <option key={l} value={l}>{l.replace("_", " ")}</option>)}
            </select>
          </label>
          {f.lifecycle === "recurring" && (
            <label className="text-sm font-medium">Recurrence (months) *
              <input type="number" min={1} value={f.recurrence_months}
                onChange={set("recurrence_months")} className={inputCls + " mt-1"} />
            </label>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Applicability
            <select value={f.applicability} onChange={set("applicability")} className={inputCls + " mt-1"}>
              <option value="applicable">Applicable</option>
              <option value="not_applicable">Not applicable</option>
            </select>
          </label>
          <label className="text-sm font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} />
          </label>
        </div>
        {f.applicability === "not_applicable" && (
          <div className="grid grid-cols-2 gap-3 rounded-md border border-bd bg-canvas p-2.5">
            <label className="text-sm font-medium">Why not applicable? *
              <textarea value={f.na_justification} onChange={set("na_justification")}
                className={cn(inputCls, "mt-1 min-h-[44px]")} /></label>
            <label className="text-sm font-medium">Reactivation trigger
              <input value={f.reactivation_trigger} onChange={set("reactivation_trigger")}
                className={inputCls + " mt-1"} placeholder="e.g. Cloud adoption" /></label>
          </div>
        )}
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <button disabled={disabled} onClick={() => create.mutate()}
          className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create control"}</button>
      </div>
    </Modal>
  );
}

export default function Controls() {
  const can = useCan();
  const nav = useNavigate();
  const [tab, setTab] = useState<"framework" | "crosswalk">("framework");
  const [domain, setDomain] = useState<string>("");
  const [adding, setAdding] = useState(false);

  const domains = useQuery({ queryKey: ["domains"], queryFn: () => get<Domain[]>("/library/domains") });
  const controls = useQuery({ queryKey: ["controls", domain], queryFn: () => get<Control[]>(`/library/controls${domain ? `?domain_code=${domain}` : ""}`) });
  const xwalk = useQuery({ queryKey: ["crosswalk"], queryFn: () => get<Xwalk>("/library/crosswalk"), enabled: tab === "crosswalk" });
  const total = useMemo(() => (domains.data ?? []).reduce((s, d) => s + d.control_count, 0), [domains.data]);
  const openControl = (id: string) => nav(`/controls/view/${id}`);

  return (
    <>
      <PageHead eyebrow="Standard control framework" title="Controls"
        lead="Your own master list of controls. Every bank's checklist points map onto these, so one answer serves every audit."
        action={
          <div className="flex gap-2">
            {/* P5-S9: certification readiness lives beside Controls rather than in the
                sidebar — a framework is a lens over this library, not a separate module. */}
            <Link to="/frameworks" className="btn">Certifications</Link>
            {can("controls", "add") && (
              <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New control</button>
            )}
          </div>} />

      <div className="mb-5 flex gap-1 border-b border-bd">
        {(["framework", "crosswalk"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-sm font-medium capitalize",
              tab === t ? "border-accent text-ink" : "border-transparent text-txt2")}>
            {t === "crosswalk" ? "Bank crosswalk" : "Framework"}
          </button>
        ))}
      </div>

      {tab === "framework" ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[250px_1fr] md:items-start">
          <div className="card p-2">
            <button onClick={() => setDomain("")}
              className={cn("flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-label font-medium",
                domain === "" ? "bg-[rgba(249,115,22,0.09)] font-semibold text-ink" : "text-txt2 hover:bg-canvas")}>
              All domains <span className="ml-auto text-caption text-txt3 tnum">{total}</span>
            </button>
            {(domains.data ?? []).map((d) => (
              <button key={d.id} onClick={() => setDomain(d.code)}
                className={cn("flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-label font-medium",
                  domain === d.code ? "bg-[rgba(249,115,22,0.09)] font-semibold text-ink" : "text-txt2 hover:bg-canvas")}>
                <span className="font-mono text-caption text-accent">{d.code}</span>
                <span className="truncate">{d.name}</span>
                <span className="ml-auto text-caption text-txt3 tnum">{d.control_count}</span>
              </button>
            ))}
          </div>

          {controls.isLoading ? <Loading /> : (
            <Table head={["Ref", "Control", "Lifecycle", "Applicability", "Mapped"]}>
              {(controls.data ?? []).map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-canvas" onClick={() => openControl(c.id)}>
                  <Td className="font-mono font-semibold">{c.code}</Td>
                  <Td className="font-medium">{c.statement}
                    {c.applicability !== "applicable" && <span className="ml-2 rounded bg-na-bg px-1.5 py-0.5 text-micro text-na">dormant</span>}
                  </Td>
                  <Td><Pill tone={c.lifecycle === "per_audit" ? "na" : "warn"}>{lifecycleLabel(c)}</Pill></Td>
                  <Td><Pill tone={c.applicability === "applicable" ? "applicable" : "na"}>
                    {c.applicability === "applicable" ? "Applicable" : "Dormant"}</Pill></Td>
                  <Td><span className="rounded bg-canvas px-2 py-0.5 text-caption text-txt2">{c.mapped_count} pts</span></Td>
                </tr>
              ))}
            </Table>
          )}
        </div>
      ) : (
        <>
          <p className="mb-3 max-w-[70ch] text-sm text-txt2">
            Read one row across: answer the standard control once and it satisfies every bank point in that row.
          </p>
          {xwalk.isLoading || !xwalk.data ? <Loading /> : (
            <div className="overflow-x-auto rounded-xl border border-bd bg-paper">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className={cn(TH, "sticky left-0 z-10 min-w-[240px]")}>Standard control</th>
                    {xwalk.data.columns.map((c) => (
                      <th key={c.id} className={TH}>
                        {c.bank_name}<span className="block font-normal normal-case text-txt3">{c.version_label}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {xwalk.data.rows.map((r) => (
                    <tr key={r.control_id} className="cursor-pointer hover:bg-canvas" onClick={() => openControl(r.control_id)}>
                      <Td className="sticky left-0 bg-paper">
                        <div className="font-mono text-label font-semibold text-accent">{r.code}</div>
                        <div className="text-caption text-txt3">{r.statement}</div>
                      </Td>
                      {xwalk.data!.columns.map((c) => {
                        const pts = r.cells[c.id] ?? [];
                        return (
                          <Td key={c.id}>
                            {pts.length === 0
                              ? <span className="font-mono text-txt3">—</span>
                              : pts.map((p, i) => (
                                <span key={i} className="mr-1 inline-block rounded bg-ok-bg px-1.5 py-[1px] font-mono text-caption font-semibold text-ok">{p}</span>
                              ))}
                          </Td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {adding && <AddControlModal domains={domains.data ?? []} onClose={() => setAdding(false)} />}
    </>
  );
}
