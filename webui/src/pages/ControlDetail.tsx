import { useState } from "react";
import { Plus } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { useDebounced } from "../lib/useDebounced";
import { OwnerSelect } from "./Registers";
import { Card, cn, inputCls, Loading, Pill, Table, Td } from "../lib/ui";

/**
 * The control master-record page (P4-S5). Used to be a cramped Drawer — a control is the
 * unit the whole "answer once, reuse everywhere" premise rests on, and cramming its stock
 * answer, mapped bank points, risks, obligations, evidence and policies into 560px did not
 * leave room to actually edit any of it. This is a full page, reachable and shareable at
 * /controls/view/:id (P4-S3's deep-link convention), modelled on DocumentDetail.tsx.
 */

type Domain = { id: string; code: string; name: string };
type Control = {
  id: string; code: string; statement: string; guidance: string | null;
  lifecycle: string; recurrence_months: number | null;
  applicability: string; na_justification: string | null; reactivation_trigger: string | null;
  stock_response: string | null; stock_comment: string | null;
  owner_person_id: string | null; domain_id: string; domain_name: string;
  status: string; created_at: string; updated_at: string;
};
type MappedPoint = {
  question_id: string; bank_name: string; number: string; text: string;
  confidence: number; status: string;
};
type LinkedRisk = {
  id: string; reference: string | null; title: string;
  inherent_score: number | null; residual_score: number | null; status: string;
};
type LinkedObligation = { id: string; requirement: string; regulator: string | null; status: string };
type LinkedEvidence = {
  id: string; title: string; evidence_type: string; medium: string;
  valid_until: string | null; status: string;
};
type LinkedDocument = {
  id: string; title: string; document_type: string; status: string;
  published_version: string | null;
};
type LinkedClause = {
  id: string; ref: string; title: string;
  framework_id: string; framework_code: string; framework_name: string;
};
type Framework = { id: string; code: string; name: string; version: string | null };
type Clause = { id: string; ref: string; title: string };
type Detail = Control & {
  mapped_points: MappedPoint[]; linked_risks: LinkedRisk[]; linked_obligations: LinkedObligation[];
  linked_evidence: LinkedEvidence[]; linked_documents: LinkedDocument[];
  linked_clauses: LinkedClause[];
};
type Evidence = { id: string; title: string; evidence_type: string };
type Doc = { id: string; title: string; document_type: string };

const LIFECYCLE = ["one_time", "recurring", "per_audit"] as const;
const STOCK = ["yes", "partial", "no", "na"] as const;
const stockTone = (v: string | null) =>
  v === "yes" ? "ok" : v === "partial" ? "warn" : v === "no" ? "bad" : v === "na" ? "na" : "na";

const lifecycleLabel = (c: Control) =>
  c.lifecycle === "recurring" ? `Recurring · every ${c.recurrence_months} months`
  : c.lifecycle === "one_time" ? "One-time" : "Per audit";

// ─────────────────────────────────────────────────────── edit form
function EditForm({ doc, domains, onDone }: { doc: Detail; domains: Domain[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    domain_id: doc.domain_id, code: doc.code, statement: doc.statement,
    guidance: doc.guidance ?? "", lifecycle: doc.lifecycle,
    recurrence_months: doc.recurrence_months?.toString() ?? "",
    applicability: doc.applicability, na_justification: doc.na_justification ?? "",
    reactivation_trigger: doc.reactivation_trigger ?? "",
    owner_person_id: doc.owner_person_id ?? "",
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const save = useMutation({
    mutationFn: () => api.patch(`/library/controls/${doc.id}`, {
      ...f, recurrence_months: f.recurrence_months ? +f.recurrence_months : null }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["control", doc.id] });
      qc.invalidateQueries({ queryKey: ["controls"] });
      if (r.data.stale_prefilled?.count > 0) {
        const names = r.data.stale_prefilled.assessments.map((a: any) => a.bank_name).join(", ");
        setErr(`Saved — note: ${r.data.stale_prefilled.count} open audit(s) already prefilled `
              + `from the old stock answer and were not changed (${names}).`);
      }
      onDone();
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });

  return (
    <Card>
      <div className="eyebrow mb-3">Edit control</div>
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Domain
            <select value={f.domain_id} onChange={set("domain_id")} className={inputCls + " mt-1"}>
              {domains.map((d) => <option key={d.id} value={d.id}>{d.code} — {d.name}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium">Reference code
            <input value={f.code} onChange={set("code")} className={inputCls + " mt-1"} />
          </label>
        </div>
        <label className="text-sm font-medium">Statement
          <textarea value={f.statement} onChange={set("statement")}
            className={cn(inputCls, "mt-1 min-h-[64px]")} />
        </label>
        <label className="text-sm font-medium">Guidance <span className="text-txt3">(optional)</span>
          <textarea value={f.guidance} onChange={set("guidance")}
            className={cn(inputCls, "mt-1 min-h-[48px]")} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Lifecycle
            <select value={f.lifecycle} onChange={set("lifecycle")} className={inputCls + " mt-1"}>
              {LIFECYCLE.map((l) => <option key={l} value={l}>{l.replace("_", " ")}</option>)}
            </select>
          </label>
          {f.lifecycle === "recurring" && (
            <label className="text-sm font-medium">Recurrence (months)
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
            <label className="text-sm font-medium">Why not applicable?
              <textarea value={f.na_justification} onChange={set("na_justification")}
                className={cn(inputCls, "mt-1 min-h-[44px]")} />
            </label>
            <label className="text-sm font-medium">Reactivation trigger
              <input value={f.reactivation_trigger} onChange={set("reactivation_trigger")}
                className={inputCls + " mt-1"} placeholder="e.g. Cloud adoption" />
            </label>
          </div>
        )}
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <div className="flex items-center gap-2">
          <button disabled={save.isPending
              || !f.statement.trim() || !f.code.trim()
              || (f.lifecycle === "recurring" && !f.recurrence_months)
              || (f.applicability === "not_applicable" && !f.na_justification.trim())}
            onClick={() => save.mutate()} className="btn btn-primary disabled:opacity-50">
            {save.isPending ? "Saving…" : "Save changes"}</button>
          <button onClick={onDone} className="btn">Cancel</button>
        </div>
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────── stock answer
function StockAnswer({ doc, canEdit }: { doc: Detail; canEdit: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(doc.stock_response ?? "");
  const [comment, setComment] = useState(doc.stock_comment ?? "");
  const [err, setErr] = useState("");

  const save = useMutation({
    mutationFn: () => api.patch(`/library/controls/${doc.id}`, {
      stock_response: val || null, stock_comment: comment || null }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["control", doc.id] });
      setEditing(false);
      if (r.data.stale_prefilled?.count > 0) {
        setErr(`${r.data.stale_prefilled.count} open audit(s) already prefilled from the `
              + `old answer were not changed — update them by hand if needed.`);
      } else setErr("");
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });

  return (
    <Card>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="eyebrow">Stock answer — answer once, reuse everywhere</div>
        {canEdit && !editing && (
          <button onClick={() => setEditing(true)} className="text-label font-medium text-accent">Edit stock answer</button>
        )}
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <div className="flex gap-1.5">
            {STOCK.map((s) => (
              <button key={s} onClick={() => setVal(s)}
                className={cn("rounded-md border px-3 py-1.5 text-label font-semibold capitalize",
                  val === s ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd text-txt2")}>
                {s}</button>
            ))}
          </div>
          <textarea value={comment} onChange={(e) => setComment(e.target.value)}
            placeholder="Comment shown wherever this prefills…"
            className={cn(inputCls, "min-h-[56px]")} />
          <div className="flex gap-2">
            <button disabled={save.isPending} onClick={() => save.mutate()}
              className="btn btn-primary disabled:opacity-50">{save.isPending ? "Saving…" : "Save"}</button>
            <button onClick={() => setEditing(false)} className="btn">Cancel</button>
          </div>
        </div>
      ) : doc.stock_response ? (
        <div className="flex items-start gap-3">
          <Pill tone={stockTone(doc.stock_response)}>{doc.stock_response.toUpperCase()}</Pill>
          {doc.stock_comment && <p className="text-sm text-txt2">{doc.stock_comment}</p>}
        </div>
      ) : (
        <div className="text-label text-txt3">No stock answer set yet — every mapped bank point needs answering by hand until one is.</div>
      )}
      {err && <div className="mt-2 rounded-md bg-warn-bg px-3 py-2 text-label text-warn">{err}</div>}
    </Card>
  );
}

// ─────────────────────────────────────────────────────── evidence / document pickers
function AttachEvidence({ controlId, linked, canEdit }:
  { controlId: string; linked: LinkedEvidence[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  // P4-S8: this picker had no filter of ANY kind — it listed the whole vault and you
  // scrolled. The `q` in the key is mandatory: five other components read ["evidence", …]
  // and a filtered result under the bare key would truncate all of them.
  const list = useQuery({
    queryKey: ["evidence", dq], enabled: picking,
    queryFn: () => get<Evidence[]>(`/evidence?${new URLSearchParams(dq ? { q: dq } : {})}`),
    placeholderData: keepPreviousData,
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["control", controlId] });
    qc.invalidateQueries({ queryKey: ["evidence"] });        // the artifact's own page
    qc.invalidateQueries({ queryKey: ["evidence-detail"] });
  };
  const link = useMutation({
    mutationFn: (evidence_id: string) => api.post(`/library/controls/${controlId}/evidence`, { evidence_id }),
    onSuccess: () => { done(); setPicking(false); setTerm(""); },
  });
  const unlink = useMutation({
    mutationFn: (evidence_id: string) => api.delete(`/library/controls/${controlId}/evidence/${evidence_id}`),
    onSuccess: done,
  });
  const linkedIds = new Set(linked.map((e) => e.id));

  return (
    <Card>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="eyebrow">Evidence</div>
        {canEdit && <button onClick={() => setPicking((s) => !s)} className="text-label font-medium text-accent"><Plus size={15} strokeWidth={2.4} /> Attach</button>}
      </div>
      {linked.length === 0 && <div className="text-label text-txt3">No evidence attached yet.</div>}
      {linked.map((e) => (
        <div key={e.id} className="flex items-center gap-2.5 border-t border-bd py-2 text-label first:border-t-0">
          <Link to={`/evidence/view/${e.id}`} className="min-w-0 flex-1 truncate font-medium text-ink hover:underline">{e.title}</Link>
          <span className="text-txt3">{e.evidence_type}</span>
          <Pill tone={e.status === "expired" ? "bad" : e.status === "expiring" ? "warn" : "ok"}>{e.status}</Pill>
          {canEdit && <button onClick={() => unlink.mutate(e.id)} className="text-caption text-bad hover:underline">remove</button>}
        </div>
      ))}
      {picking && (() => {
        const pickable = (list.data ?? []).filter((e) => !linkedIds.has(e.id));
        return (
          <div className="mt-2">
            <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
              placeholder="Search the vault…" className={inputCls} />
            <div className="mt-2 max-h-48 overflow-y-auto rounded-md border border-bd">
              {/* three states, not one: an in-flight search must not read as "your vault
                  is empty", and "everything is already attached" is not "no match" */}
              {list.isPending ? (
                <div className="px-3 py-2 text-label text-txt3">Searching…</div>
              ) : pickable.length === 0 ? (
                <div className="px-3 py-2 text-label text-txt3">
                  {dq ? "Nothing in the vault matches that."
                      : list.data?.length ? "Everything in the vault is already attached."
                      : "No evidence in the vault yet."}</div>
              ) : pickable.map((e) => (
                <button key={e.id} onClick={() => link.mutate(e.id)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-label hover:bg-canvas">
                  <span>{e.title}</span><span className="text-txt3">attach →</span>
                </button>))}
            </div>
          </div>);
      })()}
    </Card>
  );
}

function AttachDocument({ controlId, linked, canEdit }:
  { controlId: string; linked: LinkedDocument[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const list = useQuery({ queryKey: ["documents"], queryFn: () => get<Doc[]>("/documents"), enabled: picking });
  const link = useMutation({
    mutationFn: (document_id: string) => api.post(`/library/controls/${controlId}/documents`, { document_id }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["control", controlId] }); setPicking(false); },
  });
  const unlink = useMutation({
    mutationFn: (document_id: string) => api.delete(`/library/controls/${controlId}/documents/${document_id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["control", controlId] }),
  });
  const linkedIds = new Set(linked.map((d) => d.id));

  return (
    <Card>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="eyebrow">Policies & procedures</div>
        {canEdit && <button onClick={() => setPicking((s) => !s)} className="text-label font-medium text-accent"><Plus size={15} strokeWidth={2.4} /> Attach</button>}
      </div>
      {linked.length === 0 && <div className="text-label text-txt3">No policy documents this yet.</div>}
      {linked.map((d) => (
        <div key={d.id} className="flex items-center gap-2.5 border-t border-bd py-2 text-label first:border-t-0">
          <Link to={`/documents/${d.id}`} className="min-w-0 flex-1 truncate font-medium text-ink hover:underline">{d.title}</Link>
          <span className="text-txt3 capitalize">{d.document_type.toLowerCase()}</span>
          <Pill tone={d.status === "ARCHIVED" ? "na" : d.published_version ? "ok" : "warn"}>
            {d.status === "ARCHIVED" ? "Archived" : d.published_version ? `v${d.published_version}` : "unpublished"}
          </Pill>
          {canEdit && <button onClick={() => unlink.mutate(d.id)} className="text-caption text-bad hover:underline">remove</button>}
        </div>
      ))}
      {picking && (
        <div className="mt-2 max-h-48 overflow-y-auto rounded-md border border-bd">
          {(list.data ?? []).filter((d) => !linkedIds.has(d.id)).map((d) => (
            <button key={d.id} onClick={() => link.mutate(d.id)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-label hover:bg-canvas">
              <span>{d.title}</span><span className="text-txt3">attach →</span>
            </button>
          ))}
          {list.data?.length === 0 && <div className="px-3 py-2 text-label text-txt3">No documents yet.</div>}
        </div>
      )}
    </Card>
  );
}

/**
 * Which certification clauses this control satisfies (P5-S9).
 *
 * The card that makes the whole design visible. One control is expected to appear against ISO
 * A.8.5 AND SOC 2 CC6.1 AND an RBI clause at the same time — that is precisely why there is
 * one control library with clause tags rather than a separate master control set per
 * certification, which would duplicate this control three times with three evidence trails.
 */
function SatisfiesClauses({ controlId, linked, canEdit }:
  { controlId: string; linked: LinkedClause[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [frameworkId, setFrameworkId] = useState("");

  const frameworks = useQuery({
    queryKey: ["frameworks"], queryFn: () => get<Framework[]>("/frameworks"),
    enabled: picking });
  const fwList = frameworks.data ?? [];
  const activeFw = frameworkId || fwList[0]?.id || "";
  const clauses = useQuery({
    queryKey: ["framework-clauses", activeFw],
    queryFn: () => get<Clause[]>(`/frameworks/${activeFw}/clauses`),
    enabled: picking && !!activeFw });

  const done = () => qc.invalidateQueries({ queryKey: ["control", controlId] });
  const link = useMutation({
    mutationFn: (clause_id: string) =>
      api.post(`/library/controls/${controlId}/clauses`, { clause_id }),
    onSuccess: () => { done(); setPicking(false); },
  });
  const unlink = useMutation({
    mutationFn: (clause_id: string) =>
      api.delete(`/library/controls/${controlId}/clauses/${clause_id}`),
    onSuccess: done,
  });

  const linkedIds = new Set(linked.map((c) => c.id));
  // grouped by framework, because "this answers three standards" is the point being made
  const byFramework = linked.reduce<Record<string, LinkedClause[]>>((acc, c) => {
    (acc[c.framework_code] ??= []).push(c);
    return acc;
  }, {});

  return (
    <Card>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="eyebrow">Satisfies</div>
        {canEdit && (
          <button onClick={() => setPicking((s) => !s)}
            className="text-label font-medium text-accent"><Plus size={15} strokeWidth={2.4} /> Map a clause</button>
        )}
      </div>
      {linked.length === 0 && (
        <div className="text-label text-txt3">
          Not mapped to any certification clause yet. Mapping it here is what makes this
          control — and its evidence — count toward ISO, SOC 2 or an RBI requirement.
        </div>
      )}
      {Object.entries(byFramework).map(([code, rows]) => (
        <div key={code} className="border-t border-bd py-2 first:border-t-0">
          <div className="mb-1 text-micro font-semibold uppercase tracking-[0.09em] text-txt3">
            {code}
          </div>
          {rows.map((c) => (
            <div key={c.id} className="flex items-baseline gap-2.5 py-0.5 text-label">
              <span className="shrink-0 font-mono text-caption font-medium">{c.ref}</span>
              <span className="min-w-0 flex-1 truncate text-txt2">{c.title}</span>
              {canEdit && (
                <button onClick={() => unlink.mutate(c.id)}
                  className="shrink-0 text-caption text-bad hover:underline">remove</button>
              )}
            </div>
          ))}
        </div>
      ))}
      {picking && (
        <div className="mt-2 rounded-md border border-bd">
          <select value={activeFw} onChange={(e) => setFrameworkId(e.target.value)}
            aria-label="Framework" className={inputCls + " rounded-b-none border-0 border-b"}>
            {fwList.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <div className="max-h-48 overflow-y-auto">
            {(clauses.data ?? []).filter((c) => !linkedIds.has(c.id)).map((c) => (
              <button key={c.id} onClick={() => link.mutate(c.id)}
                className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-label hover:bg-canvas">
                <span className="shrink-0 font-mono text-caption font-medium">{c.ref}</span>
                <span className="min-w-0 flex-1 truncate text-txt2">{c.title}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

// ─────────────────────────────────────────────────────── page
export default function ControlDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const can = useCan();
  const [editing, setEditing] = useState(false);

  const { data: doc, isLoading } = useQuery({
    queryKey: ["control", id], queryFn: () => get<Detail>(`/library/controls/${id}`) });
  const domains = useQuery({ queryKey: ["domains"], queryFn: () => get<Domain[]>("/library/domains") });

  const retire = useMutation({
    mutationFn: () => api.delete(`/library/controls/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["control", id] }); qc.invalidateQueries({ queryKey: ["controls"] }); },
  });
  const restore = useMutation({
    mutationFn: () => api.post(`/library/controls/${id}/restore`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["control", id] }); qc.invalidateQueries({ queryKey: ["controls"] }); },
  });

  if (isLoading || !doc) return <Loading />;
  const canEdit = can("controls", "edit");

  return (
    <>
      <Link to="/controls" className="text-sm text-txt2 hover:text-ink">← All controls</Link>
      <div className="mb-1 mt-2 flex flex-wrap items-center gap-3">
        <span className="font-mono text-sm font-semibold text-accent">{doc.code}</span>
        <h1 className="text-title font-semibold tracking-[-0.01em]">{doc.statement}</h1>
        {doc.status === "retired" && <Pill tone="na">Retired</Pill>}
      </div>
      <p className="mb-4 flex flex-wrap items-center gap-x-1.5 gap-y-2 text-sm text-txt2">
        <span className="font-mono text-txt3">{doc.domain_name}</span> · {lifecycleLabel(doc)} ·{" "}
        <Pill tone={doc.applicability === "applicable" ? "applicable" : "na"}>
          {doc.applicability === "applicable" ? "Applicable" : "Not applicable · dormant"}
        </Pill>
        {canEdit && !editing && (
          <button onClick={() => setEditing(true)} className="btn ml-2 py-1.5">Edit control</button>
        )}
        {can("controls", "delete") && doc.status === "active" && (
          <button onClick={() => retire.mutate()} disabled={retire.isPending}
            className="btn py-1.5 text-bad hover:border-bad disabled:opacity-50">Retire</button>
        )}
        {canEdit && doc.status === "retired" && (
          <button onClick={() => restore.mutate()} disabled={restore.isPending}
            className="btn py-1.5 disabled:opacity-50">Restore</button>
        )}
      </p>

      {editing && <div className="mb-4">
        <EditForm doc={doc} domains={domains.data ?? []} onDone={() => setEditing(false)} />
      </div>}

      {doc.guidance && !editing && (
        <Card className="mb-4"><div className="eyebrow mb-1">Guidance</div>
          <p className="text-sm text-txt2">{doc.guidance}</p></Card>
      )}

      {doc.applicability !== "applicable" && !editing && (
        <Card className="mb-4 border-l-[3px] border-l-na">
          <div className="eyebrow mb-1">Dormant · reactivation trigger</div>
          <Pill tone="warn">{doc.reactivation_trigger}</Pill>
          {doc.na_justification && <p className="mt-2 text-label text-txt2">{doc.na_justification}</p>}
        </Card>
      )}

      <div className="mb-4"><StockAnswer doc={doc} canEdit={canEdit} /></div>

      <Card className="mb-4">
        <div className="eyebrow mb-2.5">Mapped bank points</div>
        {doc.mapped_points.length === 0 && <div className="text-label text-txt3">No bank points mapped yet.</div>}
        {doc.mapped_points.length > 0 && (
          <Table head={["Bank", "Point", "Confidence", "Status"]}>
            {doc.mapped_points.map((m, i) => (
              <tr key={i}>
                <Td className="font-medium">{m.bank_name}</Td>
                <Td className="min-w-0"><span className="font-mono text-txt3">#{m.number}</span>{" "}{m.text}</Td>
                <Td className="font-mono text-txt3">{(m.confidence * 100).toFixed(0)}%</Td>
                <Td><Pill tone={m.status === "confirmed" ? "ok" : "warn"}>{m.status}</Pill></Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {(doc.linked_risks?.length ?? 0) > 0 && (
          <Card>
            <div className="eyebrow mb-2.5">Risks this control treats</div>
            {doc.linked_risks.map((r) => (
              <Link key={r.id} to={`/risks/view/${r.id}`}
                className="flex items-center gap-2.5 border-t border-bd py-2 text-label first:border-t-0 hover:bg-canvas">
                {r.reference && <span className="font-mono text-txt3">{r.reference}</span>}
                <span className="min-w-0 flex-1 truncate font-medium text-ink">{r.title}</span>
                {r.inherent_score != null && (
                  <span className="shrink-0 rounded bg-canvas px-1.5 py-0.5 font-mono text-caption text-txt2">
                    {r.inherent_score}{r.residual_score != null && ` → ${r.residual_score}`}
                  </span>)}
                <Pill tone={r.status === "OPEN" ? "warn" : "ok"}>{r.status.charAt(0) + r.status.slice(1).toLowerCase()}</Pill>
              </Link>
            ))}
          </Card>
        )}
        {(doc.linked_obligations?.length ?? 0) > 0 && (
          <Card>
            <div className="eyebrow mb-2.5">Obligations this control satisfies</div>
            {doc.linked_obligations.map((o) => (
              <div key={o.id} className="flex items-center gap-2.5 border-t border-bd py-2 text-label first:border-t-0">
                {o.regulator && <span className="shrink-0 rounded bg-ink px-1.5 py-0.5 text-micro font-bold text-white">{o.regulator}</span>}
                <span className="min-w-0 flex-1 truncate">{o.requirement}</span>
                <Pill tone={o.status === "COMPLIANT" ? "ok" : o.status === "NON_COMPLIANT" ? "bad" : "warn"}>
                  {o.status.replace(/_/g, " ").toLowerCase()}</Pill>
              </div>
            ))}
          </Card>
        )}
        <AttachEvidence controlId={doc.id} linked={doc.linked_evidence} canEdit={canEdit} />
        <AttachDocument controlId={doc.id} linked={doc.linked_documents} canEdit={canEdit} />
        <SatisfiesClauses controlId={doc.id} linked={doc.linked_clauses ?? []} canEdit={canEdit} />
      </div>
    </>
  );
}
