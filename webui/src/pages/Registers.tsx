import { FormEvent, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { Card, cn, Drawer, inputCls, Loading, Modal, Pill, Table, Td } from "../lib/ui";

// ─────────────────────────────────────────────────────────── types
type Person = { id: string; full_name: string; effective_state: string };
type Risk = {
  id: string; reference: string | null; title: string; category: string | null;
  inherent_score: number | null; inherent_band: string | null;
  residual_score: number | null; residual_band: string | null;
  treatment: string | null; status: string; owner_name: string | null; link_count: number;
};
type LinkRow = { id: string; target_kind: string; target_id: string; label: string | null; note: string | null };
type RiskDetail = Risk & {
  description: string | null; note: string | null;
  inherent_likelihood: number | null; inherent_impact: number | null;
  residual_likelihood: number | null; residual_impact: number | null; links: LinkRow[];
};
type Asset = {
  id: string; name: string; asset_type: string; criticality: string | null;
  owner_name: string | null; location: string | null; quantity: number; data_types_stored: string[];
};
type AssetDetail = Asset & { description: string | null; data: { name: string; classification: string | null }[] };
type DataItem = {
  id: string; name: string; classification: string; owner_name: string | null; retention_note: string | null;
};

/**
 * Deep-linkable drawer state (P4-S3). `/risks/view/:id` opens the drawer; closing it returns
 * to the list URL. Previously every drawer was `useState`, so a row could not be linked to,
 * shared, or reopened by the back button.
 */
function useDrawerRoute(base: string) {
  const { id } = useParams();
  const nav = useNavigate();
  const open = (next: string | null) => nav(next ? `${base}/view/${next}` : base);
  return [id ?? null, open] as const;
}

// ─────────────────────────────────────────────────────────── shared bits
const BAND_TONE: Record<string, string> = { LOW: "ok", MEDIUM: "warn", HIGH: "bad", CRITICAL: "bad" };
const CRIT_TONE: Record<string, string> = { LOW: "na", MEDIUM: "warn", HIGH: "bad", CRITICAL: "bad" };
const CLASS_TONE: Record<string, string> = { PUBLIC: "na", INTERNAL: "info", CONFIDENTIAL: "warn", SECRET: "bad" };
const TREATMENTS = ["MITIGATED", "ACCEPTED", "AVOIDED", "TRANSFERRED"];
const CRITICALITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const CLASSES = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"];
const cap = (s: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : "—");

function ScoreCell({ score, band }: { score: number | null; band: string | null }) {
  if (score == null) return <span className="text-txt3">—</span>;
  return <Pill tone={BAND_TONE[band ?? ""] ?? "na"}>{score} · {cap(band)}</Pill>;
}

/**
 * A dropdown backed by an admin-editable vocabulary (P4-S3). Free-text categories produced
 * "Access control" / "access-control" / "Acess Control" in the same column, which cannot be
 * grouped or reported on. `allowBlank` keeps the field optional.
 */
export function LookupSelect({ kind, value, onChange, className }: {
  kind: string; value: string; onChange: (v: string) => void; className?: string;
}) {
  const q = useQuery({
    queryKey: ["lookups", kind],
    queryFn: () => get<{ kinds: Record<string, { label: string; values: { id: string; value: string }[] }> }>(
      `/lookups?kind=${kind}`),
  });
  const values = q.data?.kinds?.[kind]?.values ?? [];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className={(className ?? inputCls) + " mt-1"}>
      <option value="">— none —</option>
      {values.map((v) => <option key={v.id} value={v.value}>{v.value}</option>)}
      {/* a value saved before it left the list stays selectable */}
      {value && !values.some((v) => v.value === value) && <option value={value}>{value}</option>}
    </select>
  );
}

function OwnerSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const active = (people.data ?? []).filter((p) => p.effective_state === "ACTIVE");
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls + " mt-1"}>
      <option value="">— unassigned —</option>
      {active.map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
    </select>
  );
}

const LI = (v: number | null) => (v == null ? "—" : v);
function LikelihoodImpact({ l, i, onL, onI }:
  { l: string; i: string; onL: (v: string) => void; onI: (v: string) => void }) {
  const sel = (val: string, on: (v: string) => void) => (
    <select value={val} onChange={(e) => on(e.target.value)} className={inputCls + " mt-1"}>
      <option value="">—</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
    </select>
  );
  return (
    <div className="grid grid-cols-2 gap-2">
      <label className="text-[12px] text-txt2">Likelihood {sel(l, onL)}</label>
      <label className="text-[12px] text-txt2">Impact {sel(i, onI)}</label>
    </div>
  );
}

// ─────────────────────────────────────────────────────────── new risk
function NewRiskModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ title: "", reference: "", category: "", owner_person_id: "",
    il: "", ii: "", rl: "", ri: "", treatment: "", note: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/risks", {
      title: f.title, reference: f.reference || null, category: f.category || null,
      owner_person_id: f.owner_person_id || null, treatment: f.treatment || null, note: f.note || null,
      inherent_likelihood: f.il ? +f.il : null, inherent_impact: f.ii ? +f.ii : null,
      residual_likelihood: f.rl ? +f.rl : null, residual_impact: f.ri ? +f.ri : null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["risks"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New risk">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Unauthorised access to bank CMS" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Reference
            <input value={f.reference} onChange={(e) => set("reference")(e.target.value)}
              className={inputCls + " mt-1"} placeholder="R-001" /></label>
          <label className="text-[13px] font-medium">Category
            <LookupSelect kind="risk_category" value={f.category} onChange={set("category")} /></label>
        </div>
        <label className="text-[13px] font-medium">Owner
          <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-md border border-bd p-2.5">
            <div className="eyebrow mb-1">Inherent</div>
            <LikelihoodImpact l={f.il} i={f.ii} onL={set("il")} onI={set("ii")} /></div>
          <div className="rounded-md border border-bd p-2.5">
            <div className="eyebrow mb-1">Residual (after treatment)</div>
            <LikelihoodImpact l={f.rl} i={f.ri} onL={set("rl")} onI={set("ri")} /></div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Treatment
            <select value={f.treatment} onChange={(e) => set("treatment")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{TREATMENTS.map((t) => <option key={t} value={t}>{cap(t)}</option>)}
            </select></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.title || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create risk"}</button>
      </form>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────────── risk drawer + links
function AddLink({ riskId }: { riskId: string }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState("CONTROL");
  const [target, setTarget] = useState("");
  const controls = useQuery({ queryKey: ["controls-lite"], enabled: kind === "CONTROL",
    queryFn: () => get<{ id: string; code: string; statement: string }[]>("/library/controls") });
  const docs = useQuery({ queryKey: ["documents"], enabled: kind === "DOCUMENT",
    queryFn: () => get<{ id: string; title: string }[]>("/documents") });
  const assets = useQuery({ queryKey: ["assets"], enabled: kind === "ASSET",
    queryFn: () => get<Asset[]>("/assets") });
  const opts = kind === "CONTROL" ? (controls.data ?? []).map((c) => ({ id: c.id, label: `${c.code} — ${c.statement}` }))
    : kind === "DOCUMENT" ? (docs.data ?? []).map((d) => ({ id: d.id, label: d.title }))
    : (assets.data ?? []).map((a) => ({ id: a.id, label: a.name }));
  const add = useMutation({
    mutationFn: () => api.post(`/risks/${riskId}/links`, { target_kind: kind, target_id: target }),
    onSuccess: () => { setTarget(""); qc.invalidateQueries({ queryKey: ["risk", riskId] });
      qc.invalidateQueries({ queryKey: ["risks"] }); },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={kind} onChange={(e) => { setKind(e.target.value); setTarget(""); }}
        className={cn(inputCls, "w-auto")}>
        {["CONTROL", "DOCUMENT", "ASSET"].map((k) => <option key={k} value={k}>{cap(k)}</option>)}
      </select>
      <select value={target} onChange={(e) => setTarget(e.target.value)} className={cn(inputCls, "min-w-[180px] flex-1")}>
        <option value="">— pick a {kind.toLowerCase()} —</option>
        {opts.map((o) => <option key={o.id} value={o.id}>{o.label.slice(0, 60)}</option>)}
      </select>
      <button disabled={!target || add.isPending} onClick={() => add.mutate()}
        className="btn shrink-0 disabled:opacity-50">Link</button>
    </div>
  );
}

function RiskDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: r } = useQuery({ queryKey: ["risk", id], queryFn: () => get<RiskDetail>(`/risks/${id}`) });
  const del = useMutation({
    mutationFn: () => api.delete(`/risks/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["risks"] }); onClose(); },
  });
  const unlink = useMutation({
    mutationFn: (linkId: string) => api.delete(`/risks/${id}/links/${linkId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["risk", id] }); qc.invalidateQueries({ queryKey: ["risks"] }); },
  });
  if (!r) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`RISK${r.reference ? " · " + r.reference : ""}`} title={r.title}>
      <div className="flex flex-wrap gap-2">
        <Pill tone={r.status === "OPEN" ? "warn" : "ok"}>{cap(r.status)}</Pill>
        {r.treatment && <Pill tone="info">{cap(r.treatment)}</Pill>}
        {r.category && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] text-txt2">{r.category}</span>}
      </div>
      {r.description && <p className="text-[13px] text-txt2">{r.description}</p>}
      <div className="grid grid-cols-2 gap-3">
        <Card>
          <div className="eyebrow mb-1.5">Inherent</div>
          <ScoreCell score={r.inherent_score} band={r.inherent_band} />
          <div className="mt-1 text-[11.5px] text-txt3">L {LI(r.inherent_likelihood)} × I {LI(r.inherent_impact)}</div>
        </Card>
        <Card>
          <div className="eyebrow mb-1.5">Residual</div>
          <ScoreCell score={r.residual_score} band={r.residual_band} />
          <div className="mt-1 text-[11.5px] text-txt3">L {LI(r.residual_likelihood)} × I {LI(r.residual_impact)}</div>
        </Card>
      </div>
      <Card>
        <div className="eyebrow mb-2">Links · {r.links.length}</div>
        {r.links.length === 0 && <p className="mb-2 text-[12.5px] text-txt3">Not linked to anything yet. Link a control to answer "what treats this risk?" — it shows on the control too.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {r.links.map((l) => (
            <div key={l.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
              <span className="rounded bg-canvas px-1.5 py-0.5 text-[10px] font-semibold text-txt2">{cap(l.target_kind)}</span>
              <span className="min-w-0 flex-1 truncate">{l.label ?? l.target_id}</span>
              <button onClick={() => unlink.mutate(l.id)} className="shrink-0 text-txt3 hover:text-bad">✕</button>
            </div>
          ))}
        </div>
        <AddLink riskId={id} />
      </Card>
      {r.owner_name && <div className="text-[12.5px] text-txt2">Owner · <b>{r.owner_name}</b></div>}
      {r.note && <Card><div className="eyebrow mb-1">Note</div><p className="text-[12.5px] text-txt2">{r.note}</p></Card>}
      <button onClick={() => del.mutate()} className="mt-2 text-[12px] text-txt3 hover:text-bad">Delete this risk</button>
    </Drawer>
  );
}

// ─────────────────────────────────────────────────────────── risks tab
export function RisksTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/risks");
  const { data, isLoading } = useQuery({ queryKey: ["risks"], queryFn: () => get<Risk[]>("/risks") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">
        {can("risks", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New risk</button>
        )}
      </div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No risks yet. This is the register a bank asks for first — add your top risks, score them, and link the controls that treat them.
        </div>
      ) : (
        <Table head={["Ref", "Risk", "Inherent", "Residual", "Treatment", "Owner", "Links", "Status"]}>
          {rows.map((r) => (
            <tr key={r.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(r.id)}>
              <Td className="font-mono text-txt3">{r.reference ?? "—"}</Td>
              <Td className="font-medium">{r.title}</Td>
              <Td><ScoreCell score={r.inherent_score} band={r.inherent_band} /></Td>
              <Td><ScoreCell score={r.residual_score} band={r.residual_band} /></Td>
              <Td className="text-txt2">{cap(r.treatment)}</Td>
              <Td className="text-txt2">{r.owner_name ?? "—"}</Td>
              <Td className="text-txt2 tnum">{r.link_count}</Td>
              <Td><Pill tone={r.status === "OPEN" ? "warn" : "ok"}>{cap(r.status)}</Pill></Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewRiskModal onClose={() => setAdding(false)} />}
      {openId && <RiskDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ─────────────────────────────────────────────────────────── assets
function NewAssetModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const dataItems = useQuery({ queryKey: ["data-items"], queryFn: () => get<DataItem[]>("/data-items") });
  const [f, setF] = useState({ name: "", asset_type: "VIRTUAL", owner_person_id: "",
    criticality: "", location: "", quantity: "1" });
  const [dataTypes, setDataTypes] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const toggle = (name: string) =>
    setDataTypes((d) => d.includes(name) ? d.filter((x) => x !== name) : [...d, name]);
  const create = useMutation({
    mutationFn: () => api.post("/assets", {
      name: f.name, asset_type: f.asset_type, owner_person_id: f.owner_person_id || null,
      criticality: f.criticality || null, location: f.location || null,
      quantity: f.quantity ? +f.quantity : 1, data_types_stored: dataTypes }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["assets"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.name) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New asset">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="CMS Server" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Type
            <select value={f.asset_type} onChange={(e) => set("asset_type")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="VIRTUAL">Virtual</option><option value="PHYSICAL">Physical</option></select></label>
          <label className="text-[13px] font-medium">Criticality
            <select value={f.criticality} onChange={(e) => set("criticality")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-[13px] font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
          <label className="text-[13px] font-medium">Location
            <input value={f.location} onChange={(e) => set("location")(e.target.value)}
              className={inputCls + " mt-1"} placeholder="AWS ap-south-1" /></label>
        </div>
        <div>
          <div className="text-[13px] font-medium">Data it stores</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {(dataItems.data ?? []).map((d) => (
              <button type="button" key={d.id} onClick={() => toggle(d.name)}
                className={cn("rounded-full border px-2.5 py-1 text-[12px]",
                  dataTypes.includes(d.name) ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd text-txt2 hover:bg-canvas")}>
                {d.name} <span className="text-txt3">· {cap(d.classification)}</span>
              </button>))}
            {(dataItems.data ?? []).length === 0 && <span className="text-[12px] text-txt3">Add data items in the Data tab first.</span>}
          </div>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.name || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create asset"}</button>
      </form>
    </Modal>
  );
}

function AssetDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: a } = useQuery({ queryKey: ["asset", id], queryFn: () => get<AssetDetail>(`/assets/${id}`) });
  const del = useMutation({ mutationFn: () => api.delete(`/assets/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["assets"] }); onClose(); } });
  if (!a) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`ASSET · ${cap(a.asset_type)}`} title={a.name}>
      <div className="flex flex-wrap gap-2">
        {a.criticality && <Pill tone={CRIT_TONE[a.criticality]}>{cap(a.criticality)}</Pill>}
        {a.location && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] text-txt2">{a.location}</span>}
        <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] text-txt2">×{a.quantity}</span>
      </div>
      {a.description && <p className="text-[13px] text-txt2">{a.description}</p>}
      <Card>
        <div className="eyebrow mb-2">Data stored here</div>
        {a.data.length === 0 ? <p className="text-[12.5px] text-txt3">No data items linked.</p> : (
          <div className="flex flex-col gap-1.5">
            {a.data.map((d) => (
              <div key={d.name} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
                <span className="flex-1">{d.name}</span>
                {d.classification
                  ? <Pill tone={CLASS_TONE[d.classification]}>{cap(d.classification)}</Pill>
                  : <span className="text-[11px] text-txt3">not in inventory</span>}
              </div>))}
          </div>)}
      </Card>
      {a.owner_name && <div className="text-[12.5px] text-txt2">Owner · <b>{a.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-[12px] text-txt3 hover:text-bad">Delete this asset</button>
    </Drawer>
  );
}

export function AssetsTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/assets");
  const { data, isLoading } = useQuery({ queryKey: ["assets"], queryFn: () => get<Asset[]>("/assets") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("assets", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New asset</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No assets yet. Track your servers, laptops and services — what they are, how critical, and what data they hold.
        </div>
      ) : (
        <Table head={["Asset", "Type", "Criticality", "Data", "Owner", "Location"]}>
          {rows.map((a) => (
            <tr key={a.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(a.id)}>
              <Td className="font-medium">{a.name}</Td>
              <Td className="capitalize text-txt2">{a.asset_type.toLowerCase()}</Td>
              <Td>{a.criticality ? <Pill tone={CRIT_TONE[a.criticality]}>{cap(a.criticality)}</Pill> : <span className="text-txt3">—</span>}</Td>
              <Td className="text-txt2 tnum">{a.data_types_stored.length || "—"}</Td>
              <Td className="text-txt2">{a.owner_name ?? "—"}</Td>
              <Td className="text-txt2">{a.location ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewAssetModal onClose={() => setAdding(false)} />}
      {openId && <AssetDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ─────────────────────────────────────────────────────────── data inventory
function NewDataModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", classification: "INTERNAL", owner_person_id: "", retention_note: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/data-items", {
      name: f.name, classification: f.classification,
      owner_person_id: f.owner_person_id || null, retention_note: f.retention_note || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["data-items"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.name) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New data item">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Bank customer data" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Classification
            <select value={f.classification} onChange={(e) => set("classification")(e.target.value)} className={inputCls + " mt-1"}>
              {CLASSES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-[13px] font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        </div>
        <label className="text-[13px] font-medium">Retention
          <input value={f.retention_note} onChange={(e) => set("retention_note")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="7 years, then purge" /></label>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.name || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create data item"}</button>
      </form>
    </Modal>
  );
}

export function DataTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["data-items"], queryFn: () => get<DataItem[]>("/data-items") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("data", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New data item</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No data inventory yet. List the kinds of data you hold and how each is classified — assets link to these.
        </div>
      ) : (
        <Table head={["Data item", "Classification", "Owner", "Retention"]}>
          {rows.map((d) => (
            <tr key={d.id} className="hover:bg-canvas">
              <Td className="font-medium">{d.name}</Td>
              <Td><Pill tone={CLASS_TONE[d.classification]}>{cap(d.classification)}</Pill></Td>
              <Td className="text-txt2">{d.owner_name ?? "—"}</Td>
              <Td className="text-txt2">{d.retention_note ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewDataModal onClose={() => setAdding(false)} />}
    </>
  );
}

// ═══════════════════════════════════════════════════════════ Sprint 4b: third parties, obligations, incidents
type ThirdParty = {
  id: string; name: string; category: string | null; criticality: string | null; status: string;
  business_owner_name: string | null; parent_name: string | null; parent_third_party_id: string | null;
};
type Agreement = { id: string; kind: string; reference: string | null; valid_until: string | null; expiry_status: string };
type Assessment = {
  id: string; assessed_at: string | null; expires_at: string | null; outcome: string | null;
  data_sensitivity: string | null; business_impact: string | null; expiry_status: string;
};
type TreeNode = { id: string; name: string; criticality: string | null; status: string; children: TreeNode[] };
type ThirdPartyDetail = ThirdParty & {
  legal_name: string | null; security_owner_name: string | null; countries: string[]; certifications: string[];
  agreements: Agreement[]; assessments: Assessment[]; children: { id: string; name: string }[];
};
type Obligation = {
  id: string; area: string | null; requirement: string; regulator: string | null; type: string | null;
  status: string; owner_name: string | null; control_count: number;
};
type ObligationDetail = Obligation & {
  source: string | null; next_review_date: string | null;
  controls: { id: string; code: string; statement: string }[];
};
type Incident = {
  id: string; reference: string | null; title: string; severity: string | null; status: string;
  detected_at: string | null; owner_name: string | null;
};
type IncidentDetail = Incident & {
  description: string | null; resolved_at: string | null; root_cause: string | null; lessons_learnt: string | null;
};

const EXP_TONE: Record<string, string> = { expired: "bad", expiring: "warn", valid: "ok", no_expiry: "na" };
const OBS_STATUS = ["COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT"];
const OBS_TONE: Record<string, string> = { COMPLIANT: "ok", PARTIALLY_COMPLIANT: "warn", NON_COMPLIANT: "bad" };
const INC_STATUS = ["OPEN", "INVESTIGATING", "RESOLVED", "CLOSED"];
const INC_TONE: Record<string, string> = { OPEN: "bad", INVESTIGATING: "warn", RESOLVED: "info", CLOSED: "ok" };
const AGREEMENT_KINDS = ["DPA", "BAA", "NDA", "SLA", "MSA", "OTHER"];
const OUTCOMES = ["PASS", "PASS_WITH_ACTIONS", "FAIL"];
const nice = (s: string | null) => (s ? s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "—");

// ── third-party tree
function Tree({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  return (
    <div>
      <div className="flex items-center gap-2 py-1 text-[13px]" style={{ paddingLeft: depth * 18 }}>
        {depth > 0 && <span className="text-txt3">└─</span>}
        <span className="font-medium">{node.name}</span>
        {node.criticality && <Pill tone={CRIT_TONE[node.criticality]}>{cap(node.criticality)}</Pill>}
        {depth === 0 && <span className="text-[11px] text-txt3">(you → this vendor)</span>}
        {depth === 1 && <span className="text-[11px] text-txt3">4th party</span>}
      </div>
      {node.children.map((c) => <Tree key={c.id} node={c} depth={depth + 1} />)}
    </div>
  );
}

function NewThirdPartyModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const parents = useQuery({ queryKey: ["third-parties"], queryFn: () => get<ThirdParty[]>("/third-parties") });
  const [f, setF] = useState({ name: "", legal_name: "", category: "", criticality: "",
    status: "ACTIVE", parent_third_party_id: "", business_owner_person_id: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/third-parties", {
      name: f.name, legal_name: f.legal_name || null, category: f.category || null,
      criticality: f.criticality || null, status: f.status,
      parent_third_party_id: f.parent_third_party_id || null,
      business_owner_person_id: f.business_owner_person_id || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["third-parties"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.name) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New third party">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Cloud Vendor A" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Category
            <LookupSelect kind="third_party_category" value={f.category} onChange={set("category")} /></label>
          <label className="text-[13px] font-medium">Criticality
            <select value={f.criticality} onChange={(e) => set("criticality")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-[13px] font-medium">Business owner
            <OwnerSelect value={f.business_owner_person_id} onChange={set("business_owner_person_id")} /></label>
          <label className="text-[13px] font-medium">Sub-processor of
            <select value={f.parent_third_party_id} onChange={(e) => set("parent_third_party_id")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">— none (direct vendor) —</option>
              {(parents.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        </div>
        <p className="rounded-md bg-canvas px-3 py-2 text-[11.5px] text-txt2">
          "Sub-processor of" builds the <b>4th-party chain</b> a bank asks you to disclose.</p>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.name || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create vendor"}</button>
      </form>
    </Modal>
  );
}

function AddAgreement({ tpId }: { tpId: string }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState("DPA");
  const [until, setUntil] = useState("");
  const add = useMutation({
    mutationFn: () => api.post(`/third-parties/${tpId}/agreements`, { kind, valid_until: until || null }),
    onSuccess: () => { setUntil(""); qc.invalidateQueries({ queryKey: ["third-party", tpId] }); },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={kind} onChange={(e) => setKind(e.target.value)} className={cn(inputCls, "w-auto")}>
        {AGREEMENT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}</select>
      <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} className={cn(inputCls, "w-auto")} />
      <button disabled={add.isPending} onClick={() => add.mutate()} className="btn shrink-0">Add agreement</button>
    </div>
  );
}

function AddAssessment({ tpId }: { tpId: string }) {
  const qc = useQueryClient();
  const [expires, setExpires] = useState("");
  const [outcome, setOutcome] = useState("PASS");
  const add = useMutation({
    mutationFn: () => api.post(`/third-parties/${tpId}/assessments`, { expires_at: expires || null, outcome }),
    onSuccess: () => { setExpires(""); qc.invalidateQueries({ queryKey: ["third-party", tpId] }); },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className={cn(inputCls, "w-auto")}>
        {OUTCOMES.map((o) => <option key={o} value={o}>{nice(o)}</option>)}</select>
      <span className="text-[12px] text-txt3">expires</span>
      <input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} className={cn(inputCls, "w-auto")} />
      <button disabled={add.isPending} onClick={() => add.mutate()} className="btn shrink-0">Add assessment</button>
    </div>
  );
}

function ThirdPartyDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: tp } = useQuery({ queryKey: ["third-party", id], queryFn: () => get<ThirdPartyDetail>(`/third-parties/${id}`) });
  const tree = useQuery({ queryKey: ["tp-tree", id], queryFn: () => get<TreeNode>(`/third-parties/${id}/tree`) });
  const del = useMutation({ mutationFn: () => api.delete(`/third-parties/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["third-parties"] }); onClose(); } });
  const rmAgreement = useMutation({ mutationFn: (aid: string) => api.delete(`/third-parties/${id}/agreements/${aid}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["third-party", id] }) });
  if (!tp) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`THIRD PARTY${tp.parent_name ? " · sub-processor of " + tp.parent_name : ""}`} title={tp.name}>
      <div className="flex flex-wrap gap-2">
        <Pill tone={tp.status === "ACTIVE" ? "ok" : tp.status === "TERMINATED" ? "bad" : "warn"}>{nice(tp.status)}</Pill>
        {tp.criticality && <Pill tone={CRIT_TONE[tp.criticality]}>{cap(tp.criticality)}</Pill>}
        {tp.category && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] text-txt2">{tp.category}</span>}
      </div>
      {(tree.data && (tree.data.children.length > 0)) && (
        <Card>
          <div className="eyebrow mb-2">Sub-processor chain (4th parties)</div>
          <Tree node={tree.data} />
        </Card>)}
      <Card>
        <div className="mb-2 flex items-center justify-between">
          <div className="eyebrow">Agreements</div></div>
        {tp.agreements.length === 0 && <p className="mb-2 text-[12.5px] text-txt3">No DPA/BAA/NDA on file.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {tp.agreements.map((a) => (
            <div key={a.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
              <span className="rounded bg-canvas px-1.5 py-0.5 text-[10px] font-semibold text-txt2">{a.kind}</span>
              <span className="flex-1 text-txt2">{a.valid_until ? `until ${a.valid_until.slice(0, 10)}` : "no expiry"}</span>
              {a.valid_until && <Pill tone={EXP_TONE[a.expiry_status] ?? "na"}>{a.expiry_status}</Pill>}
              <button onClick={() => rmAgreement.mutate(a.id)} className="text-txt3 hover:text-bad">✕</button>
            </div>))}
        </div>
        <AddAgreement tpId={id} />
      </Card>
      <Card>
        <div className="eyebrow mb-2">Security assessments</div>
        {tp.assessments.length === 0 && <p className="mb-2 text-[12.5px] text-txt3">No assessment recorded — a bank will ask when this vendor was last reviewed.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {tp.assessments.map((a) => (
            <div key={a.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
              <span className="flex-1">{a.outcome ? nice(a.outcome) : "—"} · expires {a.expires_at ? a.expires_at.slice(0, 10) : "—"}</span>
              {a.expires_at && <Pill tone={EXP_TONE[a.expiry_status] ?? "na"}>{a.expiry_status}</Pill>}
            </div>))}
        </div>
        <AddAssessment tpId={id} />
      </Card>
      {tp.business_owner_name && <div className="text-[12.5px] text-txt2">Business owner · <b>{tp.business_owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-[12px] text-txt3 hover:text-bad">Delete this vendor</button>
    </Drawer>
  );
}

export function ThirdPartiesTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/third-parties");
  const { data, isLoading } = useQuery({ queryKey: ["third-parties"], queryFn: () => get<ThirdParty[]>("/third-parties") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("third_parties", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New third party</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No vendors yet. Track who you rely on, their sub-processors (the bank's 4th parties), and when each was last assessed.
        </div>
      ) : (
        <Table head={["Vendor", "Category", "Criticality", "Status", "Owner", "Sub-processor of"]}>
          {rows.map((v) => (
            <tr key={v.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(v.id)}>
              <Td className="font-medium">{v.name}</Td>
              <Td className="text-txt2">{v.category ?? "—"}</Td>
              <Td>{v.criticality ? <Pill tone={CRIT_TONE[v.criticality]}>{cap(v.criticality)}</Pill> : <span className="text-txt3">—</span>}</Td>
              <Td><Pill tone={v.status === "ACTIVE" ? "ok" : v.status === "TERMINATED" ? "bad" : "warn"}>{nice(v.status)}</Pill></Td>
              <Td className="text-txt2">{v.business_owner_name ?? "—"}</Td>
              <Td className="text-txt2">{v.parent_name ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewThirdPartyModal onClose={() => setAdding(false)} />}
      {openId && <ThirdPartyDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ── obligations
function NewObligationModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ requirement: "", area: "", regulator: "RBI", type: "", status: "PARTIALLY_COMPLIANT", owner_person_id: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/obligations", {
      requirement: f.requirement, area: f.area || null, regulator: f.regulator || null,
      type: f.type || null, status: f.status, owner_person_id: f.owner_person_id || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["obligations"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.requirement) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New obligation">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Requirement *
          <textarea required value={f.requirement} onChange={(e) => set("requirement")(e.target.value)}
            className={inputCls + " mt-1 min-h-[70px]"} placeholder="Outsourcing risk management per RBI IT outsourcing directions" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Area
            <input value={f.area} onChange={(e) => set("area")(e.target.value)} className={inputCls + " mt-1"} placeholder="Outsourcing" /></label>
          <label className="text-[13px] font-medium">Regulator
            <input value={f.regulator} onChange={(e) => set("regulator")(e.target.value)} className={inputCls + " mt-1"} placeholder="RBI" /></label>
          <label className="text-[13px] font-medium">Type
            <select value={f.type} onChange={(e) => set("type")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option><option value="LEGAL">Legal</option><option value="CONTRACTUAL">Contractual</option></select></label>
          <label className="text-[13px] font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.requirement || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create obligation"}</button>
      </form>
    </Modal>
  );
}

function LinkControl({ obligationId }: { obligationId: string }) {
  const qc = useQueryClient();
  const [cid, setCid] = useState("");
  const controls = useQuery({ queryKey: ["controls-lite"], queryFn: () => get<{ id: string; code: string; statement: string }[]>("/library/controls") });
  const add = useMutation({
    mutationFn: () => api.post(`/obligations/${obligationId}/controls`, { control_id: cid }),
    onSuccess: () => { setCid(""); qc.invalidateQueries({ queryKey: ["obligation", obligationId] }); qc.invalidateQueries({ queryKey: ["obligations"] }); },
  });
  return (
    <div className="flex items-center gap-2">
      <select value={cid} onChange={(e) => setCid(e.target.value)} className={cn(inputCls, "flex-1")}>
        <option value="">— pick a control —</option>
        {(controls.data ?? []).map((c) => <option key={c.id} value={c.id}>{`${c.code} — ${c.statement}`.slice(0, 60)}</option>)}
      </select>
      <button disabled={!cid || add.isPending} onClick={() => add.mutate()} className="btn shrink-0 disabled:opacity-50">Link</button>
    </div>
  );
}

function ObligationDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: o } = useQuery({ queryKey: ["obligation", id], queryFn: () => get<ObligationDetail>(`/obligations/${id}`) });
  const del = useMutation({ mutationFn: () => api.delete(`/obligations/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["obligations"] }); onClose(); } });
  const unlink = useMutation({ mutationFn: (cid: string) => api.delete(`/obligations/${id}/controls/${cid}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["obligation", id] }); qc.invalidateQueries({ queryKey: ["obligations"] }); } });
  const setStatus = useMutation({ mutationFn: (status: string) => api.patch(`/obligations/${id}`, { status }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["obligation", id] }); qc.invalidateQueries({ queryKey: ["obligations"] }); } });
  if (!o) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`OBLIGATION${o.regulator ? " · " + o.regulator : ""}`} title={o.requirement}>
      <div className="flex flex-wrap items-center gap-2">
        <select value={o.status} onChange={(e) => setStatus.mutate(e.target.value)}
          className={cn(inputCls, "w-auto")}>
          {OBS_STATUS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
        {o.type && <Pill tone="info">{cap(o.type)}</Pill>}
        {o.area && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] text-txt2">{o.area}</span>}
      </div>
      <Card>
        <div className="eyebrow mb-2">Controls that satisfy this · {o.controls.length}</div>
        {o.controls.length === 0 && <p className="mb-2 text-[12.5px] text-txt3">Link the controls that demonstrate compliance — they'll drive the SoA later.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {o.controls.map((c) => (
            <div key={c.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
              <span className="font-mono text-txt3">{c.code}</span>
              <span className="min-w-0 flex-1 truncate">{c.statement}</span>
              <button onClick={() => unlink.mutate(c.id)} className="text-txt3 hover:text-bad">✕</button>
            </div>))}
        </div>
        <LinkControl obligationId={id} />
      </Card>
      {o.owner_name && <div className="text-[12.5px] text-txt2">Owner · <b>{o.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-[12px] text-txt3 hover:text-bad">Delete this obligation</button>
    </Drawer>
  );
}

// Hidden from navigation for now (P4-S3) but kept whole — the tables, API and this
// screen all still work; it just has no menu entry until the module is revisited.
export function ObligationsTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["obligations"], queryFn: () => get<Obligation[]>("/obligations") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("obligations", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New obligation</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No obligations yet. This is where <b>RBI</b> (and contractual) requirements live — each linked to the controls that satisfy it.
        </div>
      ) : (
        <Table head={["Area", "Requirement", "Regulator", "Type", "Controls", "Status"]}>
          {rows.map((o) => (
            <tr key={o.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(o.id)}>
              <Td className="text-txt2">{o.area ?? "—"}</Td>
              <Td className="max-w-[360px] truncate font-medium">{o.requirement}</Td>
              <Td className="text-txt2">{o.regulator ?? "—"}</Td>
              <Td className="text-txt2">{cap(o.type)}</Td>
              <Td className="text-txt2 tnum">{o.control_count}</Td>
              <Td><Pill tone={OBS_TONE[o.status] ?? "na"}>{nice(o.status)}</Pill></Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewObligationModal onClose={() => setAdding(false)} />}
      {openId && <ObligationDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ── incidents
function NewIncidentModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ title: "", reference: "", severity: "", detected_at: "", description: "", owner_person_id: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/incidents", {
      title: f.title, reference: f.reference || null, severity: f.severity || null,
      detected_at: f.detected_at || null, description: f.description || null,
      owner_person_id: f.owner_person_id || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["incidents"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New incident">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="CMS brief outage" /></label>
        <div className="grid grid-cols-3 gap-3">
          <label className="text-[13px] font-medium">Reference
            <input value={f.reference} onChange={(e) => set("reference")(e.target.value)} className={inputCls + " mt-1"} placeholder="INC-001" /></label>
          <label className="text-[13px] font-medium">Severity
            <select value={f.severity} onChange={(e) => set("severity")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-[13px] font-medium">Detected
            <input type="date" value={f.detected_at} onChange={(e) => set("detected_at")(e.target.value)} className={inputCls + " mt-1"} /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.title || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create incident"}</button>
      </form>
    </Modal>
  );
}

function IncidentDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: inc } = useQuery({ queryKey: ["incident", id], queryFn: () => get<IncidentDetail>(`/incidents/${id}`) });
  const [rca, setRca] = useState<string | null>(null);
  const [lessons, setLessons] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const patch = useMutation({
    mutationFn: (body: any) => api.patch(`/incidents/${id}`, body),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["incident", id] }); qc.invalidateQueries({ queryKey: ["incidents"] }); },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });
  const del = useMutation({ mutationFn: () => api.delete(`/incidents/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["incidents"] }); onClose(); } });
  if (!inc) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  const rcaVal = rca ?? inc.root_cause ?? "";
  const lessonsVal = lessons ?? inc.lessons_learnt ?? "";
  return (
    <Drawer open onClose={onClose} sub={`INCIDENT${inc.reference ? " · " + inc.reference : ""}`} title={inc.title}>
      <div className="flex flex-wrap items-center gap-2">
        <select value={inc.status} onChange={(e) => patch.mutate({ status: e.target.value })} className={cn(inputCls, "w-auto")}>
          {INC_STATUS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
        {inc.severity && <Pill tone={CRIT_TONE[inc.severity]}>{cap(inc.severity)}</Pill>}
        {inc.detected_at && <span className="text-[12px] text-txt3">detected {inc.detected_at.slice(0, 10)}</span>}
      </div>
      {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
      {inc.description && <p className="text-[13px] text-txt2">{inc.description}</p>}
      <Card>
        <div className="eyebrow mb-2">Root cause analysis — the part a bank actually reads</div>
        <label className="text-[12px] text-txt2">Root cause
          <textarea value={rcaVal} onChange={(e) => setRca(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="Why did it happen?" /></label>
        <label className="mt-3 block text-[12px] text-txt2">Lessons learnt
          <textarea value={lessonsVal} onChange={(e) => setLessons(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="What changes so it doesn't recur?" /></label>
        <button onClick={() => patch.mutate({ root_cause: rcaVal || null, lessons_learnt: lessonsVal || null })}
          className="btn btn-primary mt-3">Save RCA</button>
        <p className="mt-2 text-[11px] text-txt3">An incident can't be marked <b>Closed</b> until it has a root cause.</p>
      </Card>
      {inc.owner_name && <div className="text-[12.5px] text-txt2">Owner · <b>{inc.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-[12px] text-txt3 hover:text-bad">Delete this incident</button>
    </Drawer>
  );
}

export function IncidentsTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/incidents");
  const { data, isLoading } = useQuery({ queryKey: ["incidents"], queryFn: () => get<Incident[]>("/incidents") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("incidents", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New incident</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-[13px] text-txt2">
          No incidents logged. Banks ask for the <b>RCA</b>, not the ticket — record what happened, the root cause, and the lessons learnt.
        </div>
      ) : (
        <Table head={["Ref", "Incident", "Severity", "Detected", "Owner", "Status"]}>
          {rows.map((i) => (
            <tr key={i.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(i.id)}>
              <Td className="font-mono text-txt3">{i.reference ?? "—"}</Td>
              <Td className="font-medium">{i.title}</Td>
              <Td>{i.severity ? <Pill tone={CRIT_TONE[i.severity]}>{cap(i.severity)}</Pill> : <span className="text-txt3">—</span>}</Td>
              <Td className="text-txt2">{i.detected_at?.slice(0, 10) ?? "—"}</Td>
              <Td className="text-txt2">{i.owner_name ?? "—"}</Td>
              <Td><Pill tone={INC_TONE[i.status] ?? "na"}>{nice(i.status)}</Pill></Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewIncidentModal onClose={() => setAdding(false)} />}
      {openId && <IncidentDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ─────────────────────────────────────────────────────────── page
/**
 * P4-S3: the registers became five top-level modules (/risks, /assets, /data,
 * /third-parties, /incidents). Obligations is hidden for now — its tables and API remain.
 * This route only exists so old links and bookmarks still land somewhere sensible.
 */
export default function Registers() {
  return <Navigate to="/risks" replace />;
}
