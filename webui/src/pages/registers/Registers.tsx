import { FormEvent, useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile, errText, fetchBlob, get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { useDebounced } from "../../lib/useDebounced";
import { AttachmentLink } from "../../components/AttachmentLink";
import { DataTable, Column } from "../../components/DataTable";
import { BulkImportModal } from "../../components/BulkImportModal";
import { Card, cn, Drawer, inputCls, Loading, Modal, Pill, Table, Td } from "../../lib/ui";

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
  reported_by_name: string | null; reviewed_by_name: string | null;
  evidence: EvidenceRow[];
};
type EvidenceRow = { id: string; title: string; evidence_type: string; valid_until: string | null };
type Asset = {
  id: string; name: string; asset_type: string; criticality: string | null;
  owner_name: string | null; location: string | null; quantity: number; data_types_stored: string[];
};
type AssetDetail = Asset & {
  description: string | null; data: { name: string; classification: string | null }[];
  vendor_name: string | null; vendor_third_party_id: string | null; subtype: string | null;
  manufacturer: string | null; model: string | null; serial_number: string | null;
  hostname: string | null; ip_address: string | null;
  cloud_provider: string | null; service_url: string | null;
  photo_file_id: string | null; photo_name: string | null;
};
type DataItem = {
  id: string; name: string; classification: string; owner_name: string | null; retention_note: string | null;
  data_type: string | null;
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

export function OwnerSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
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
      <label className="text-label text-txt2">Likelihood {sel(l, onL)}</label>
      <label className="text-label text-txt2">Impact {sel(i, onI)}</label>
    </div>
  );
}

// ─────────────────────────────────────────────────────────── new risk
function NewRiskModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ title: "", reference: "", category: "", owner_person_id: "",
    reported_by_person_id: "", reviewed_by_person_id: "",
    il: "", ii: "", rl: "", ri: "", treatment: "", note: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/risks", {
      title: f.title, reference: f.reference || null, category: f.category || null,
      owner_person_id: f.owner_person_id || null, treatment: f.treatment || null, note: f.note || null,
      reported_by_person_id: f.reported_by_person_id || null,
      reviewed_by_person_id: f.reviewed_by_person_id || null,
      inherent_likelihood: f.il ? +f.il : null, inherent_impact: f.ii ? +f.ii : null,
      residual_likelihood: f.rl ? +f.rl : null, residual_impact: f.ri ? +f.ri : null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["risks"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New risk">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Unauthorised access to bank CMS" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Reference
            <input value={f.reference} onChange={(e) => set("reference")(e.target.value)}
              className={inputCls + " mt-1"} placeholder="R-001" /></label>
          <label className="text-sm font-medium">Category
            <LookupSelect kind="risk_category" value={f.category} onChange={set("category")} /></label>
        </div>
        <label className="text-sm font-medium">Owner
          <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Reported by
            <OwnerSelect value={f.reported_by_person_id} onChange={set("reported_by_person_id")} /></label>
          <label className="text-sm font-medium">Reviewed by
            <OwnerSelect value={f.reviewed_by_person_id} onChange={set("reviewed_by_person_id")} /></label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-md border border-bd p-2.5">
            <div className="eyebrow mb-1">Inherent</div>
            <LikelihoodImpact l={f.il} i={f.ii} onL={set("il")} onI={set("ii")} /></div>
          <div className="rounded-md border border-bd p-2.5">
            <div className="eyebrow mb-1">Residual (after treatment)</div>
            <LikelihoodImpact l={f.rl} i={f.ri} onL={set("rl")} onI={set("ri")} /></div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Treatment
            <select value={f.treatment} onChange={(e) => set("treatment")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{TREATMENTS.map((t) => <option key={t} value={t}>{cap(t)}</option>)}
            </select></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
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

/**
 * Attach evidence from the vault to a register record (P4-S7). One component for risks and
 * incidents — both write to their own join table but the affordance is identical, and the
 * vault is only fetched once the picker is opened.
 */
function AttachEvidenceCard({ base, invalidateKey, rows }: {
  base: string; invalidateKey: unknown[]; rows: EvidenceRow[];
}) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [err, setErr] = useState("");
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  const vault = useQuery({
    // ["evidence", q] everywhere, without exception: a filtered result written under the
    // bare key would silently truncate the vault page and every other picker.
    queryKey: ["evidence", dq], enabled: picking,
    queryFn: () => get<EvidenceRow[]>(`/evidence?${new URLSearchParams(dq ? { q: dq } : {})}`),
    placeholderData: keepPreviousData,
  });
  const done = () => { qc.invalidateQueries({ queryKey: invalidateKey }); setPicking(false); };
  const attach = useMutation({
    mutationFn: (evidence_id: string) => api.post(`${base}/evidence`, { evidence_id }),
    onSuccess: () => { setErr(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not attach.")),
  });
  const detach = useMutation({
    mutationFn: (evidence_id: string) => api.delete(`${base}/evidence/${evidence_id}`),
    onSuccess: () => { setErr(""); done(); },
    // Without this a failed detach (a Viewer's 403, a dropped connection) leaves the row
    // sitting there looking attached, with nothing said. Silence reads as success.
    onError: (e: any) => setErr(errText(e, "Could not detach.")),
  });
  const linked = new Set(rows.map((r) => r.id));
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <div className="eyebrow">Evidence · {rows.length}</div>
        <button onClick={() => setPicking((v) => !v)} className="text-label font-medium text-accent">
          <Plus size={15} strokeWidth={2.4} /> Attach</button>
      </div>
      {rows.length === 0 && <p className="text-label text-txt3">Nothing attached yet.</p>}
      {/* P5-S4: was a plain unstyled <span> — the worst attachment presentation in the app
          (no link affordance, no icon, no way to actually LOOK at the proof without going
          to the vault and finding it again). `AttachmentLink` is the shared component S1
          built for exactly this. */}
      {rows.map((e) => (
        <div key={e.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
          <div className="min-w-0 flex-1"><AttachmentLink id={e.id} title={e.title} /></div>
          <span className="shrink-0 text-txt3">{nice(e.evidence_type)}</span>
          <button onClick={() => detach.mutate(e.id)} aria-label={`Detach ${e.title}`}
            className="shrink-0 text-txt3 hover:text-bad">✕</button>
        </div>))}
      {err && <div className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
      {picking && (() => {
        // Distinct states, because collapsing them lies to the user: while the search is
        // in flight `vault.data` is undefined, and rendering the empty case there says
        // "you have no evidence" to someone who has plenty.
        const pickable = (vault.data ?? []).filter((e) => !linked.has(e.id));
        return (
          <div className="mt-2">
            <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
              placeholder="Search the vault…" className={inputCls} />
            <div className="mt-2 max-h-40 overflow-y-auto rounded-md border border-bd">
              {vault.isPending ? (
                <div className="px-3 py-2 text-label text-txt3">Searching…</div>
              ) : pickable.length === 0 ? (
                <div className="px-3 py-2 text-label text-txt3">
                  {dq ? "Nothing in the vault matches that."
                      : vault.data?.length ? "Everything in the vault is already attached."
                      : "No evidence in the vault yet."}</div>
              ) : pickable.map((e) => (
                <button key={e.id} onClick={() => attach.mutate(e.id)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-label hover:bg-canvas">
                  <span>{e.title}</span><span className="text-txt3">attach →</span>
                </button>))}
            </div>
          </div>);
      })()}
    </Card>
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
        {r.category && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption text-txt2">{r.category}</span>}
      </div>
      {r.description && <p className="text-sm text-txt2">{r.description}</p>}
      <div className="grid grid-cols-2 gap-3">
        <Card>
          <div className="eyebrow mb-1.5">Inherent</div>
          <ScoreCell score={r.inherent_score} band={r.inherent_band} />
          <div className="mt-1 text-caption text-txt3">L {LI(r.inherent_likelihood)} × I {LI(r.inherent_impact)}</div>
        </Card>
        <Card>
          <div className="eyebrow mb-1.5">Residual</div>
          <ScoreCell score={r.residual_score} band={r.residual_band} />
          <div className="mt-1 text-caption text-txt3">L {LI(r.residual_likelihood)} × I {LI(r.residual_impact)}</div>
        </Card>
      </div>
      <Card>
        <div className="eyebrow mb-2">Links · {r.links.length}</div>
        {r.links.length === 0 && <p className="mb-2 text-label text-txt3">Not linked to anything yet. Link a control to answer "what treats this risk?" — it shows on the control too.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {r.links.map((l) => (
            <div key={l.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
              <span className="rounded bg-canvas px-1.5 py-0.5 text-micro font-semibold text-txt2">{cap(l.target_kind)}</span>
              <span className="min-w-0 flex-1 truncate">{l.label ?? l.target_id}</span>
              <button onClick={() => unlink.mutate(l.id)} className="shrink-0 text-txt3 hover:text-bad">✕</button>
            </div>
          ))}
        </div>
        <AddLink riskId={id} />
      </Card>
      <AttachEvidenceCard base={`/risks/${id}`} invalidateKey={["risk", id]} rows={r.evidence ?? []} />
      <div className="flex flex-col gap-1 text-label text-txt2">
        {r.owner_name && <div>Owner · <b>{r.owner_name}</b></div>}
        {r.reported_by_name && <div>Reported by · <b>{r.reported_by_name}</b></div>}
        {r.reviewed_by_name && <div>Reviewed by · <b>{r.reviewed_by_name}</b></div>}
      </div>
      {r.note && <Card><div className="eyebrow mb-1">Note</div><p className="text-label text-txt2">{r.note}</p></Card>}
      <button onClick={() => del.mutate()} className="mt-2 text-label text-txt3 hover:text-bad">Delete this risk</button>
    </Drawer>
  );
}

/**
 * The register list query, shared by all five tabs (P5-S4).
 *
 * Every one of these endpoints already accepted `?q=`; none of the screens ever sent it.
 * `q` belongs in the queryKey because several components share these caches — a filtered
 * result written under the bare key would silently truncate the list for everything else
 * reading it (the same trap `AttachEvidenceCard` documents for the evidence vault).
 */
function useRegisterList<T>(key: string, path: string, q: string) {
  return useQuery({
    queryKey: [key, q],
    queryFn: () => get<T[]>(`${path}${q ? `?${new URLSearchParams({ q })}` : ""}`),
    placeholderData: keepPreviousData,
  });
}

// ─────────────────────────────────────────────────────────── risks tab
export function RisksTab() {
  const can = useCan();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useDrawerRoute("/risks");
  const { data, isLoading } = useRegisterList<Risk>("risks", "/risks", q);
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  const columns: Column<Risk>[] = [
    { key: "ref", label: "Ref", sortValue: (r) => r.reference,
      render: (r) => <span className="font-mono text-txt3">{r.reference ?? "—"}</span> },
    { key: "title", label: "Risk", sortValue: (r) => r.title.toLowerCase(),
      render: (r) => <span className="font-medium">{r.title}</span> },
    { key: "inherent", label: "Inherent", sortValue: (r) => r.inherent_score,
      render: (r) => <ScoreCell score={r.inherent_score} band={r.inherent_band} /> },
    { key: "residual", label: "Residual", sortValue: (r) => r.residual_score,
      render: (r) => <ScoreCell score={r.residual_score} band={r.residual_band} /> },
    { key: "treatment", label: "Treatment", sortValue: (r) => r.treatment,
      render: (r) => <span className="text-txt2">{cap(r.treatment)}</span> },
    { key: "owner", label: "Owner", sortValue: (r) => r.owner_name,
      render: (r) => <span className="text-txt2">{r.owner_name ?? "—"}</span> },
    { key: "links", label: "Links", sortValue: (r) => r.link_count,
      render: (r) => <span className="text-txt2 tnum">{r.link_count}</span> },
    { key: "status", label: "Status", sortValue: (r) => r.status,
      render: (r) => <Pill tone={r.status === "OPEN" ? "warn" : "ok"}>{cap(r.status)}</Pill> },
  ];

  return (
    <>
      <div className="mb-3 flex justify-end">
        {can("risks", "add") && (
          <>
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New risk</button>
          </>
        )}
      </div>
      {rows.length === 0 && !q ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
          No risks yet. This is the register a bank asks for first — add your top risks, score them, and link the controls that treat them.
        </div>
      ) : (
        <DataTable
          rows={rows} getId={(r) => r.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search risks…"
          onRowClick={(r) => setOpenId(r.id)}
          canDelete={can("risks", "delete")}
          // A risk cited by an audit finding is refused with a 409 (see delete_risk).
          // DataTable reports each failure against its own row, which is exactly why that
          // guard had to land before this rollout — otherwise it would be a row of 500s.
          onDeleteOne={(id) => api.delete(`/risks/${id}`).then(() => {
            qc.invalidateQueries({ queryKey: ["risks"] });
          })}
          noMatchMessage={`No risks match "${q}".`}
        />
      )}
      {adding && <NewRiskModal onClose={() => setAdding(false)} />}
      {importing && (
        <BulkImportModal register="risks" onClose={() => setImporting(false)} />
      )}
      {openId && <RiskDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ─────────────────────────────────────────────────────────── assets
/** Vendors are a bounded, server-searchable list, so a plain select is right here —
 *  ControlPicker's popover exists only because /library/controls has no q= param. */
function VendorSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const vendors = useQuery({ queryKey: ["third-parties"],
    queryFn: () => get<ThirdParty[]>("/third-parties") });
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls + " mt-1"}>
      <option value="">— none —</option>
      {(vendors.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
    </select>
  );
}

const PHYSICAL_FIELDS = [
  ["manufacturer", "Manufacturer", "Dell"],
  ["model", "Model", "PowerEdge R740"],
  ["serial_number", "Serial number", "SN-99312"],
] as const;
const VIRTUAL_FIELDS = [
  ["hostname", "Hostname", "cms-prod-01"],
  ["ip_address", "IP address", "10.0.4.12"],
  ["cloud_provider", "Cloud provider", "AWS"],
  ["service_url", "Service URL", "https://cms.example.com"],
] as const;

function NewAssetModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const dataItems = useQuery({ queryKey: ["data-items"], queryFn: () => get<DataItem[]>("/data-items") });
  const [f, setF] = useState<Record<string, string>>({
    name: "", asset_type: "VIRTUAL", owner_person_id: "", criticality: "", location: "",
    quantity: "1", vendor_third_party_id: "", subtype: "",
    manufacturer: "", model: "", serial_number: "",
    hostname: "", ip_address: "", cloud_provider: "", service_url: "",
  });
  const [dataTypes, setDataTypes] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const toggle = (name: string) =>
    setDataTypes((d) => d.includes(name) ? d.filter((x) => x !== name) : [...d, name]);
  const typeFields = f.asset_type === "PHYSICAL" ? PHYSICAL_FIELDS : VIRTUAL_FIELDS;
  const create = useMutation({
    mutationFn: () => api.post("/assets", {
      name: f.name, asset_type: f.asset_type, owner_person_id: f.owner_person_id || null,
      criticality: f.criticality || null, location: f.location || null,
      quantity: f.quantity ? +f.quantity : 1, data_types_stored: dataTypes,
      vendor_third_party_id: f.vendor_third_party_id || null, subtype: f.subtype || null,
      // Send only the CURRENT type's fields. The other side's values stay in local state
      // (so a mis-click on the type toggle is recoverable) but must never reach the API —
      // the server rejects a physical asset carrying a hostname, and rightly so.
      ...Object.fromEntries(typeFields.map(([k]) => [k, f[k] || null])),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["assets"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.name) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New asset" size="lg">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="CMS Server" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Type
            <select value={f.asset_type} onChange={(e) => set("asset_type")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="VIRTUAL">Virtual</option><option value="PHYSICAL">Physical</option></select></label>
          <label className="text-sm font-medium">Sub-type
            <LookupSelect kind="asset_subtype" value={f.subtype} onChange={set("subtype")} /></label>
          <label className="text-sm font-medium">Criticality
            <select value={f.criticality} onChange={(e) => set("criticality")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-sm font-medium">Quantity
            <input type="number" min={0} value={f.quantity} onChange={(e) => set("quantity")(e.target.value)}
              className={inputCls + " mt-1"} /></label>
          <label className="text-sm font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
          <label className="text-sm font-medium">Vendor
            <VendorSelect value={f.vendor_third_party_id} onChange={set("vendor_third_party_id")} /></label>
          <label className="text-sm font-medium">Location
            <input value={f.location} onChange={(e) => set("location")(e.target.value)}
              className={inputCls + " mt-1"}
              placeholder={f.asset_type === "PHYSICAL" ? "Server room, HO" : "AWS ap-south-1"} /></label>
        </div>
        <div className="rounded-md border border-bd p-2.5">
          <div className="eyebrow mb-2">{f.asset_type === "PHYSICAL" ? "Physical detail" : "Virtual detail"}</div>
          <div className="grid grid-cols-2 gap-3">
            {typeFields.map(([key, label, ph]) => (
              <label key={key} className="text-sm font-medium">{label}
                <input value={f[key]} onChange={(e) => set(key)(e.target.value)}
                  className={inputCls + " mt-1"} placeholder={ph} /></label>
            ))}
          </div>
        </div>
        <div>
          <div className="text-sm font-medium">Data it stores</div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {(dataItems.data ?? []).map((d) => (
              <button type="button" key={d.id} onClick={() => toggle(d.name)}
                className={cn("rounded-full border px-2.5 py-1 text-label",
                  dataTypes.includes(d.name) ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd text-txt2 hover:bg-canvas")}>
                {d.name} <span className="text-txt3">· {cap(d.classification)}</span>
              </button>))}
            {(dataItems.data ?? []).length === 0 && <span className="text-label text-txt3">Add data items in the Data tab first.</span>}
          </div>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <button disabled={!f.name || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create asset"}</button>
      </form>
    </Modal>
  );
}

/**
 * A photograph of the physical unit (P4-S7). It cannot be a plain `<img src="/api/…">`:
 * the JWT lives in localStorage and is attached by the axios interceptor, so a native
 * image request arrives with no Authorization header and gets a 401. Fetch it as a blob
 * and hand the object URL to the tag instead — and revoke it, or every drawer open leaks
 * the image for the life of the page.
 */
function AssetPhoto({ id, a }: { id: string; a: AssetDetail }) {
  const qc = useQueryClient();
  const [src, setSrc] = useState("");
  const [err, setErr] = useState("");
  const fileId = a.photo_file_id;

  useEffect(() => {
    if (!fileId) { setSrc(""); return; }
    let revoke: (() => void) | null = null;
    let live = true;
    fetchBlob(`/assets/${id}/photo`).then((b) => {
      if (!live) { b.revoke(); return; }
      revoke = b.revoke; setSrc(b.objectUrl);
    }).catch(() => live && setErr("Could not load the photo."));
    return () => { live = false; revoke?.(); };
  }, [id, fileId]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["asset", id] });
  const up = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.post(`/assets/${id}/photo`, fd); },
    onSuccess: () => { setErr(""); refresh(); },
    onError: (e: any) => setErr(errText(e, "Upload failed.")),
  });
  const rm = useMutation({
    mutationFn: () => api.delete(`/assets/${id}/photo`),
    onSuccess: () => { setErr(""); refresh(); },
    onError: (e: any) => setErr(errText(e, "Could not remove.")),
  });

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <div className="eyebrow">Photo</div>
        {fileId && (
          <button onClick={() => rm.mutate()} className="text-label text-txt3 hover:text-bad">Remove</button>)}
      </div>
      {fileId ? (
        src
          ? <img src={src} alt={a.photo_name ?? a.name}
              className="max-h-52 w-full rounded-md border border-bd object-contain bg-canvas" />
          : <div className="grid h-24 place-items-center text-label text-txt3">Loading…</div>
      ) : (
        <label className="flex cursor-pointer items-center justify-center rounded-md border border-dashed border-bd py-6 text-label text-txt2 hover:border-accent hover:text-accent">
          {up.isPending ? "Uploading…"
            : <><Plus size={15} strokeWidth={2.4} /> Add a photo of this unit</>}
          <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) up.mutate(f); }} />
        </label>
      )}
      {err && <div className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
    </Card>
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
        {a.location && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption text-txt2">{a.location}</span>}
        <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption text-txt2">×{a.quantity}</span>
      </div>
      {a.description && <p className="text-sm text-txt2">{a.description}</p>}
      {/* Physical only — a photograph of a cloud service is nothing. */}
      {a.asset_type === "PHYSICAL" && <AssetPhoto id={id} a={a} />}
      {(() => {
        // only the fields that belong to THIS asset's type, and only ones with a value
        const spec = a.asset_type === "PHYSICAL" ? PHYSICAL_FIELDS : VIRTUAL_FIELDS;
        const rows = spec.filter(([k]) => (a as any)[k]);
        if (!rows.length && !a.subtype && !a.vendor_name) return null;
        return (
          <Card>
            <div className="eyebrow mb-2">{a.asset_type === "PHYSICAL" ? "Physical detail" : "Virtual detail"}</div>
            <div className="flex flex-col gap-1.5">
              {a.subtype && (
                <div className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
                  <span className="w-32 shrink-0 text-txt3">Sub-type</span><span>{a.subtype}</span>
                </div>)}
              {rows.map(([k, label]) => (
                <div key={k} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
                  <span className="w-32 shrink-0 text-txt3">{label}</span>
                  <span className="font-mono">{(a as any)[k]}</span>
                </div>))}
              {a.vendor_name && (
                <div className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
                  <span className="w-32 shrink-0 text-txt3">Vendor</span>
                  <Link to={`/third-parties/view/${a.vendor_third_party_id}`}
                    className="font-medium text-ink hover:text-accent hover:underline">{a.vendor_name}</Link>
                </div>)}
            </div>
          </Card>
        );
      })()}
      <Card>
        <div className="eyebrow mb-2">Data stored here</div>
        {a.data.length === 0 ? <p className="text-label text-txt3">No data items linked.</p> : (
          <div className="flex flex-col gap-1.5">
            {a.data.map((d) => (
              <div key={d.name} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
                <span className="flex-1">{d.name}</span>
                {d.classification
                  ? <Pill tone={CLASS_TONE[d.classification]}>{cap(d.classification)}</Pill>
                  : <span className="text-caption text-txt3">not in inventory</span>}
              </div>))}
          </div>)}
      </Card>
      {a.owner_name && <div className="text-label text-txt2">Owner · <b>{a.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-label text-txt3 hover:text-bad">Delete this asset</button>
    </Drawer>
  );
}

export function AssetsTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/assets");
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data, isLoading } = useRegisterList<Asset>("assets", "/assets", q);
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const columns: Column<Asset>[] = [
    { key: "name", label: "Asset", sortValue: (a) => a.name.toLowerCase(),
      render: (a) => <span className="font-medium">{a.name}</span> },
    { key: "type", label: "Type", sortValue: (a) => a.asset_type,
      render: (a) => <span className="capitalize text-txt2">{a.asset_type.toLowerCase()}</span> },
    { key: "crit", label: "Criticality", sortValue: (a) => a.criticality,
      render: (a) => a.criticality ? <Pill tone={CRIT_TONE[a.criticality]}>{cap(a.criticality)}</Pill> : <span className="text-txt3">—</span> },
    { key: "data", label: "Data", sortValue: (a) => a.data_types_stored.length,
      render: (a) => <span className="text-txt2 tnum">{a.data_types_stored.length || "—"}</span> },
    { key: "owner", label: "Owner", sortValue: (a) => a.owner_name,
      render: (a) => <span className="text-txt2">{a.owner_name ?? "—"}</span> },
    { key: "loc", label: "Location", sortValue: (a) => a.location,
      render: (a) => <span className="text-txt2">{a.location ?? "—"}</span> },
  ];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("assets", "add") && (
          <>
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New asset</button>
          </>
        )}</div>
      {rows.length === 0 && !q ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
          No assets yet. Track your servers, laptops and services — what they are, how critical, and what data they hold.
        </div>
      ) : (
        <DataTable rows={rows} getId={(a) => a.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search assets…"
          onRowClick={(a) => setOpenId(a.id)}
          canDelete={can("assets", "delete")}
          onDeleteOne={(id) => api.delete(`/assets/${id}`).then(() => {
            qc.invalidateQueries({ queryKey: ["assets"] }); })}
          noMatchMessage={`No assets match "${q}".`} />
      )}
      {adding && <NewAssetModal onClose={() => setAdding(false)} />}
      {importing && (
        <BulkImportModal register="assets" onClose={() => setImporting(false)} />
      )}
      {openId && <AssetDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

// ─────────────────────────────────────────────────────────── data inventory
function NewDataModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", classification: "INTERNAL", owner_person_id: "",
    retention_note: "", data_type: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/data-items", {
      name: f.name, classification: f.classification, data_type: f.data_type || null,
      owner_person_id: f.owner_person_id || null, retention_note: f.retention_note || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["data-items"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.name) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New data item">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Bank customer data" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Classification
            <select value={f.classification} onChange={(e) => set("classification")(e.target.value)} className={inputCls + " mt-1"}>
              {CLASSES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-sm font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Where it lives
            <LookupSelect kind="data_type" value={f.data_type} onChange={set("data_type")} /></label>
          <label className="text-sm font-medium">Retention
            <input value={f.retention_note} onChange={(e) => set("retention_note")(e.target.value)}
              className={inputCls + " mt-1"} placeholder="7 years, then purge" /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <button disabled={!f.name || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create data item"}</button>
      </form>
    </Modal>
  );
}

export function DataTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data, isLoading } = useRegisterList<DataItem>("data-items", "/data-items", q);
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const columns: Column<DataItem>[] = [
    { key: "name", label: "Data item", sortValue: (d) => d.name.toLowerCase(),
      render: (d) => <span className="font-medium">{d.name}</span> },
    { key: "class", label: "Classification", sortValue: (d) => d.classification,
      render: (d) => <Pill tone={CLASS_TONE[d.classification]}>{cap(d.classification)}</Pill> },
    { key: "where", label: "Where it lives", sortValue: (d) => d.data_type,
      render: (d) => <span className="text-txt2">{d.data_type ?? "—"}</span> },
    { key: "owner", label: "Owner", sortValue: (d) => d.owner_name,
      render: (d) => <span className="text-txt2">{d.owner_name ?? "—"}</span> },
    { key: "ret", label: "Retention", sortValue: (d) => d.retention_note,
      render: (d) => <span className="text-txt2">{d.retention_note ?? "—"}</span> },
  ];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("data", "add") && (
          <>
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New data item</button>
          </>
        )}</div>
      {rows.length === 0 && !q ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
          No data inventory yet. List the kinds of data you hold and how each is classified — assets link to these.
        </div>
      ) : (
        <DataTable rows={rows} getId={(d) => d.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search data items…"
          canDelete={can("data", "delete")}
          onDeleteOne={(id) => api.delete(`/data-items/${id}`).then(() => {
            qc.invalidateQueries({ queryKey: ["data-items"] }); })}
          noMatchMessage={`No data items match "${q}".`} />
      )}
      {adding && <NewDataModal onClose={() => setAdding(false)} />}
      {importing && (
        <BulkImportModal register="data-items" onClose={() => setImporting(false)} />
      )}
    </>
  );
}

// ═══════════════════════════════════════════════════════════ Sprint 4b: third parties, obligations, incidents
type ThirdParty = {
  id: string; name: string; category: string | null; criticality: string | null; status: string;
  business_owner_name: string | null; parent_name: string | null; parent_third_party_id: string | null;
};
type Agreement = { id: string; kind: string; reference: string | null; valid_until: string | null;
  expiry_status: string; file_name: string | null };
type Assessment = {
  id: string; assessed_at: string | null; expires_at: string | null; outcome: string | null;
  data_sensitivity: string | null; business_impact: string | null; expiry_status: string;
  evidence_id: string | null; evidence_title: string | null;
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
  detected_at: string | null; owner_name: string | null; category: string | null;
};
type IncidentDetail = Incident & {
  description: string | null; resolved_at: string | null; root_cause: string | null; lessons_learnt: string | null;
  resolution: string | null; corrective_action: string | null;
  events: IncidentEvent[]; evidence: EvidenceRow[];
};
type IncidentEvent = {
  id: string; event_type: string; body: string; author_kind: string;
  occurred_at: string; created_at: string; author_name: string | null;
};

const EXP_TONE: Record<string, string> = { expired: "bad", expiring: "warn", valid: "ok", no_expiry: "na" };
const OBS_STATUS = ["COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT"];
const OBS_TONE: Record<string, string> = { COMPLIANT: "ok", PARTIALLY_COMPLIANT: "warn", NON_COMPLIANT: "bad" };
const INC_STATUS = ["OPEN", "INVESTIGATING", "RESOLVED", "CLOSED"];
const INC_TONE: Record<string, string> = { OPEN: "bad", INVESTIGATING: "warn", RESOLVED: "info", CLOSED: "ok" };
const AGREEMENT_KINDS = ["DPA", "BAA", "NDA", "SLA", "MSA", "OTHER"];
const EVENT_TYPES = ["DETECTED", "CONTAINMENT", "INVESTIGATION", "CORRECTIVE_ACTION",
  "COMMUNICATION", "COMMENT", "RESOLVED", "CLOSED"];
const EVENT_DOT: Record<string, string> = {
  DETECTED: "bg-bad", CONTAINMENT: "bg-warn", INVESTIGATION: "bg-accent",
  CORRECTIVE_ACTION: "bg-accent", COMMUNICATION: "bg-txt3", COMMENT: "bg-txt3",
  RESOLVED: "bg-ok", CLOSED: "bg-ok",
};
const OUTCOMES = ["PASS", "PASS_WITH_ACTIONS", "FAIL"];
const nice = (s: string | null) => (s ? s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "—");

// ── third-party tree
function Tree({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  return (
    <div>
      <div className="flex items-center gap-2 py-1 text-sm" style={{ paddingLeft: depth * 18 }}>
        {depth > 0 && <span className="text-txt3">└─</span>}
        <span className="font-medium">{node.name}</span>
        {node.criticality && <Pill tone={CRIT_TONE[node.criticality]}>{cap(node.criticality)}</Pill>}
        {depth === 0 && <span className="text-caption text-txt3">(you → this vendor)</span>}
        {depth === 1 && <span className="text-caption text-txt3">4th party</span>}
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
        <label className="text-sm font-medium">Name *
          <input required value={f.name} onChange={(e) => set("name")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Cloud Vendor A" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Category
            <LookupSelect kind="third_party_category" value={f.category} onChange={set("category")} /></label>
          <label className="text-sm font-medium">Criticality
            <select value={f.criticality} onChange={(e) => set("criticality")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-sm font-medium">Business owner
            <OwnerSelect value={f.business_owner_person_id} onChange={set("business_owner_person_id")} /></label>
          <label className="text-sm font-medium">Sub-processor of
            <select value={f.parent_third_party_id} onChange={(e) => set("parent_third_party_id")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">— none (direct vendor) —</option>
              {(parents.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        </div>
        <p className="rounded-md bg-canvas px-3 py-2 text-caption text-txt2">
          "Sub-processor of" builds the <b>4th-party chain</b> a bank asks you to disclose.</p>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
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
  const [ref, setRef] = useState("");
  const add = useMutation({
    mutationFn: () => api.post(`/third-parties/${tpId}/agreements`,
      { kind, valid_until: until || null, reference: ref || null }),
    onSuccess: () => { setUntil(""); setRef(""); qc.invalidateQueries({ queryKey: ["third-party", tpId] }); },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={kind} onChange={(e) => setKind(e.target.value)} className={cn(inputCls, "w-auto")}>
        {AGREEMENT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}</select>
      <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Contract ref"
        className={cn(inputCls, "w-32")} />
      <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} className={cn(inputCls, "w-auto")} />
      <button disabled={add.isPending} onClick={() => add.mutate()} className="btn shrink-0">Add agreement</button>
    </div>
  );
}

/**
 * Attach the signed contract to an agreement (P4-S7). `third_party_agreements.file_id` has
 * existed since Sprint 4b with no way to populate it, so "we have a DPA" was an unevidenced
 * claim — the one thing a bank auditor always asks to see.
 */
function AgreementFile({ tpId, a }: { tpId: string; a: Agreement }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const base = `/third-parties/${tpId}/agreements/${a.id}/file`;
  const up = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.post(base, fd); },
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["third-party", tpId] }); },
    onError: (e: any) => setErr(errText(e, "Upload failed.")),
  });
  if (a.file_name)
    return (
      <button onClick={() => downloadFile(base, a.file_name!)}
        title={a.file_name} className="max-w-[9rem] shrink-0 truncate text-caption text-accent hover:underline">
        📎 {a.file_name}</button>);
  return (
    <label className="shrink-0 cursor-pointer text-caption text-txt3 hover:text-accent" title={err || undefined}>
      {up.isPending ? "uploading…" : err ? "⚠ retry"
        : <><Plus size={15} strokeWidth={2.4} /> contract</>}
      <input type="file" className="hidden" onChange={(e) => {
        const f = e.target.files?.[0]; e.target.value = ""; if (f) up.mutate(f); }} />
    </label>
  );
}

/** Point an assessment at the questionnaire or report that backs it. */
function AssessmentEvidence({ tpId, a }: { tpId: string; a: Assessment }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  // A native <select> cannot host a search box, so this one stays on the unfiltered key.
  const vault = useQuery({ queryKey: ["evidence", ""], enabled: picking,
    queryFn: () => get<EvidenceRow[]>("/evidence") });
  const setEv = useMutation({
    mutationFn: (evidence_id: string | null) =>
      api.patch(`/third-parties/${tpId}/assessments/${a.id}`, { evidence_id }),
    onSuccess: () => { setPicking(false); qc.invalidateQueries({ queryKey: ["third-party", tpId] }); },
  });
  if (a.evidence_title && !picking)
    return (
      <span className="flex shrink-0 items-center gap-1 text-caption">
        <Link to={`/evidence/view/${a.evidence_id}`} className="max-w-[8rem] truncate text-accent hover:underline"
          title={a.evidence_title}>📄 {a.evidence_title}</Link>
        <button onClick={() => setEv.mutate(null)} className="text-txt3 hover:text-bad">✕</button>
      </span>);
  if (!picking)
    return <button onClick={() => setPicking(true)} className="shrink-0 text-caption text-txt3 hover:text-accent"><Plus size={15} strokeWidth={2.4} /> evidence</button>;
  return (
    <select autoFocus aria-label="Pick evidence" defaultValue=""
      className={cn(inputCls, "w-40 shrink-0 py-1 text-caption")}
      onBlur={() => setPicking(false)}
      onChange={(e) => e.target.value && setEv.mutate(e.target.value)}>
      <option value="">— pick evidence —</option>
      {(vault.data ?? []).map((e) => <option key={e.id} value={e.id}>{e.title}</option>)}
    </select>
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
      <span className="text-label text-txt3">expires</span>
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
        {tp.category && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption text-txt2">{tp.category}</span>}
      </div>
      {(tree.data && (tree.data.children.length > 0)) && (
        <Card>
          <div className="eyebrow mb-2">Sub-processor chain (4th parties)</div>
          <Tree node={tree.data} />
        </Card>)}
      <Card>
        <div className="mb-2 flex items-center justify-between">
          <div className="eyebrow">Agreements</div></div>
        {tp.agreements.length === 0 && <p className="mb-2 text-label text-txt3">No DPA/BAA/NDA on file.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {tp.agreements.map((a) => (
            <div key={a.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
              <span className="rounded bg-canvas px-1.5 py-0.5 text-micro font-semibold text-txt2">{a.kind}</span>
              <span className="min-w-0 flex-1 truncate text-txt2">
                {a.reference && <span className="font-mono text-caption text-ink">{a.reference} · </span>}
                {a.valid_until ? `until ${a.valid_until.slice(0, 10)}` : "no expiry"}</span>
              <AgreementFile tpId={id} a={a} />
              {a.valid_until && <Pill tone={EXP_TONE[a.expiry_status] ?? "na"}>{a.expiry_status}</Pill>}
              <button onClick={() => rmAgreement.mutate(a.id)} className="shrink-0 text-txt3 hover:text-bad">✕</button>
            </div>))}
        </div>
        <AddAgreement tpId={id} />
      </Card>
      <Card>
        <div className="eyebrow mb-2">Security assessments</div>
        {tp.assessments.length === 0 && <p className="mb-2 text-label text-txt3">No assessment recorded — a bank will ask when this vendor was last reviewed.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {tp.assessments.map((a) => (
            <div key={a.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
              <span className="min-w-0 flex-1 truncate">{a.outcome ? nice(a.outcome) : "—"} · expires {a.expires_at ? a.expires_at.slice(0, 10) : "—"}</span>
              <AssessmentEvidence tpId={id} a={a} />
              {a.expires_at && <Pill tone={EXP_TONE[a.expiry_status] ?? "na"}>{a.expiry_status}</Pill>}
            </div>))}
        </div>
        <AddAssessment tpId={id} />
      </Card>
      {tp.business_owner_name && <div className="text-label text-txt2">Business owner · <b>{tp.business_owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-label text-txt3 hover:text-bad">Delete this vendor</button>
    </Drawer>
  );
}

export function ThirdPartiesTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/third-parties");
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data, isLoading } = useRegisterList<ThirdParty>("third-parties", "/third-parties", q);
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const columns: Column<ThirdParty>[] = [
    { key: "name", label: "Vendor", sortValue: (v) => v.name.toLowerCase(),
      render: (v) => <span className="font-medium">{v.name}</span> },
    { key: "cat", label: "Category", sortValue: (v) => v.category,
      render: (v) => <span className="text-txt2">{v.category ?? "—"}</span> },
    { key: "crit", label: "Criticality", sortValue: (v) => v.criticality,
      render: (v) => v.criticality ? <Pill tone={CRIT_TONE[v.criticality]}>{cap(v.criticality)}</Pill> : <span className="text-txt3">—</span> },
    { key: "status", label: "Status", sortValue: (v) => v.status,
      render: (v) => <Pill tone={v.status === "ACTIVE" ? "ok" : v.status === "TERMINATED" ? "bad" : "warn"}>{nice(v.status)}</Pill> },
    { key: "owner", label: "Owner", sortValue: (v) => v.business_owner_name,
      render: (v) => <span className="text-txt2">{v.business_owner_name ?? "—"}</span> },
    { key: "parent", label: "Sub-processor of", sortValue: (v) => v.parent_name,
      render: (v) => <span className="text-txt2">{v.parent_name ?? "—"}</span> },
  ];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("third_parties", "add") && (
          <>
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New third party</button>
          </>
        )}</div>
      {rows.length === 0 && !q ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
          No vendors yet. Track who you rely on, their sub-processors (the bank's 4th parties), and when each was last assessed.
        </div>
      ) : (
        <DataTable rows={rows} getId={(v) => v.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search vendors…"
          onRowClick={(v) => setOpenId(v.id)}
          canDelete={can("third_parties", "delete")}
          // 409 when an asset still names the vendor — delete_third_party's guard.
          onDeleteOne={(id) => api.delete(`/third-parties/${id}`).then(() => {
            qc.invalidateQueries({ queryKey: ["third-parties"] }); })}
          noMatchMessage={`No vendors match "${q}".`} />
      )}
      {adding && <NewThirdPartyModal onClose={() => setAdding(false)} />}
      {importing && (
        <BulkImportModal register="third-parties" onClose={() => setImporting(false)} />
      )}
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
        <label className="text-sm font-medium">Requirement *
          <textarea required value={f.requirement} onChange={(e) => set("requirement")(e.target.value)}
            className={inputCls + " mt-1 min-h-[70px]"} placeholder="Outsourcing risk management per RBI IT outsourcing directions" /></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Area
            <LookupSelect kind="obligation_area" value={f.area} onChange={set("area")} /></label>
          <label className="text-sm font-medium">Regulator
            <LookupSelect kind="regulator" value={f.regulator} onChange={set("regulator")} /></label>
          <label className="text-sm font-medium">Type
            <select value={f.type} onChange={(e) => set("type")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option><option value="LEGAL">Legal</option><option value="CONTRACTUAL">Contractual</option></select></label>
          <label className="text-sm font-medium">Owner
            <OwnerSelect value={f.owner_person_id} onChange={set("owner_person_id")} /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
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
        {o.area && <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption text-txt2">{o.area}</span>}
      </div>
      <Card>
        <div className="eyebrow mb-2">Controls that satisfy this · {o.controls.length}</div>
        {o.controls.length === 0 && <p className="mb-2 text-label text-txt3">Link the controls that demonstrate compliance — they'll drive the SoA later.</p>}
        <div className="mb-3 flex flex-col gap-1.5">
          {o.controls.map((c) => (
            <div key={c.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
              <span className="font-mono text-txt3">{c.code}</span>
              <span className="min-w-0 flex-1 truncate">{c.statement}</span>
              <button onClick={() => unlink.mutate(c.id)} className="text-txt3 hover:text-bad">✕</button>
            </div>))}
        </div>
        <LinkControl obligationId={id} />
      </Card>
      {o.owner_name && <div className="text-label text-txt2">Owner · <b>{o.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-label text-txt3 hover:text-bad">Delete this obligation</button>
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
          <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New obligation</button>
        )}</div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
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
  const [f, setF] = useState({ title: "", reference: "", severity: "", category: "", detected_at: "", description: "", owner_person_id: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/incidents", {
      title: f.title, reference: f.reference || null, severity: f.severity || null,
      category: f.category || null,
      detected_at: f.detected_at || null, description: f.description || null,
      owner_person_id: f.owner_person_id || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["incidents"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New incident">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="CMS brief outage" /></label>
        <div className="grid grid-cols-3 gap-3">
          <label className="text-sm font-medium">Reference
            <input value={f.reference} onChange={(e) => set("reference")(e.target.value)} className={inputCls + " mt-1"} placeholder="INC-001" /></label>
          <label className="text-sm font-medium">Severity
            <select value={f.severity} onChange={(e) => set("severity")(e.target.value)} className={inputCls + " mt-1"}>
              <option value="">—</option>{CRITICALITIES.map((c) => <option key={c} value={c}>{cap(c)}</option>)}</select></label>
          <label className="text-sm font-medium">Detected
            <input type="date" value={f.detected_at} onChange={(e) => set("detected_at")(e.target.value)} className={inputCls + " mt-1"} /></label>
          {/* P5-S4: the `incident_category` vocabulary was seeded in P4-S3 and had no column
              to write to until now. Lookup-backed like risk category and asset subtype, so
              Masters (S6) can extend it without a migration. */}
          <label className="text-sm font-medium">Category
            <LookupSelect kind="incident_category" value={f.category} onChange={set("category")} /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <button disabled={!f.title || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create incident"}</button>
      </form>
    </Modal>
  );
}

/**
 * The incident timeline (P4-S7). Entries are append-only — `incident_events` carries a
 * deny_update trigger — because a bank reading an incident record must be able to trust
 * that the sequence of events was not rewritten after the fact. To correct a mistake you
 * add another entry saying so; nothing is ever edited or removed.
 */
function IncidentTimeline({ id, events }: { id: string; events: IncidentEvent[] }) {
  const qc = useQueryClient();
  const [type, setType] = useState("COMMENT");
  const [body, setBody] = useState("");
  const [err, setErr] = useState("");
  const add = useMutation({
    mutationFn: () => api.post(`/incidents/${id}/events`, { event_type: type, body }),
    onSuccess: () => { setBody(""); setErr(""); qc.invalidateQueries({ queryKey: ["incident", id] }); },
    onError: (e: any) => setErr(errText(e, "Could not add.")),
  });
  return (
    <Card>
      <div className="eyebrow mb-2">Timeline · {events.length}</div>
      {events.length === 0 && (
        <p className="mb-3 text-label text-txt3">
          Nothing logged yet. Record when it was detected, what you did to contain it, and when it was resolved.</p>)}
      <div className="mb-3 flex flex-col">
        {events.map((e) => (
          <div key={e.id} className="flex gap-2.5 border-l border-bd pl-3 pb-2.5 last:pb-0">
            <span className={cn("mt-1.5 -ml-[17px] h-2 w-2 shrink-0 rounded-full ring-2 ring-paper",
              EVENT_DOT[e.event_type] ?? "bg-txt3")} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-caption font-semibold uppercase tracking-wide text-txt2">{nice(e.event_type)}</span>
                <span className="text-caption text-txt3">
                  {e.occurred_at.slice(0, 16).replace("T", " ")}
                  {e.author_name ? ` · ${e.author_name}` : e.author_kind === "system" ? " · system" : ""}</span>
              </div>
              <p className="whitespace-pre-wrap text-label text-ink">{e.body}</p>
            </div>
          </div>))}
      </div>
      <div className="flex flex-col gap-2 border-t border-bd pt-3">
        <select value={type} aria-label="Timeline entry type"
          onChange={(e) => setType(e.target.value)} className={cn(inputCls, "w-auto")}>
          {EVENT_TYPES.map((t2) => <option key={t2} value={t2}>{nice(t2)}</option>)}</select>
        <textarea value={body} onChange={(e) => setBody(e.target.value)}
          className={inputCls + " min-h-[52px]"} placeholder="What happened, and when?" />
        {err && <div className="rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
        <button disabled={!body.trim() || add.isPending} onClick={() => add.mutate()}
          className="btn self-start disabled:opacity-50">
          {add.isPending ? "Adding…" : "Add to timeline"}</button>
        <p className="text-caption text-txt3">Entries can't be edited or deleted — add a correction instead.</p>
      </div>
    </Card>
  );
}

function IncidentDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: inc } = useQuery({ queryKey: ["incident", id], queryFn: () => get<IncidentDetail>(`/incidents/${id}`) });
  const [rca, setRca] = useState<string | null>(null);
  const [lessons, setLessons] = useState<string | null>(null);
  const [resolution, setResolution] = useState<string | null>(null);
  const [corrective, setCorrective] = useState<string | null>(null);
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
  const resolutionVal = resolution ?? inc.resolution ?? "";
  const correctiveVal = corrective ?? inc.corrective_action ?? "";
  return (
    <Drawer open onClose={onClose} sub={`INCIDENT${inc.reference ? " · " + inc.reference : ""}`} title={inc.title}>
      <div className="flex flex-wrap items-center gap-2">
        <select value={inc.status} onChange={(e) => patch.mutate({ status: e.target.value })} className={cn(inputCls, "w-auto")}>
          {INC_STATUS.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select>
        {inc.severity && <Pill tone={CRIT_TONE[inc.severity]}>{cap(inc.severity)}</Pill>}
        {inc.detected_at && <span className="text-label text-txt3">detected {inc.detected_at.slice(0, 10)}</span>}
      </div>
      {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
      {inc.description && <p className="text-sm text-txt2">{inc.description}</p>}
      <Card>
        <div className="eyebrow mb-2">Root cause analysis — the part a bank actually reads</div>
        <label className="text-label text-txt2">Root cause
          <textarea value={rcaVal} onChange={(e) => setRca(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="Why did it happen?" /></label>
        <label className="mt-3 block text-label text-txt2">Corrective action
          <textarea value={correctiveVal} onChange={(e) => setCorrective(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="What was fixed, by whom, by when?" /></label>
        <label className="mt-3 block text-label text-txt2">Resolution
          <textarea value={resolutionVal} onChange={(e) => setResolution(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="How was it finally put right?" /></label>
        <label className="mt-3 block text-label text-txt2">Lessons learnt
          <textarea value={lessonsVal} onChange={(e) => setLessons(e.target.value)}
            className={inputCls + " mt-1 min-h-[64px]"} placeholder="What changes so it doesn't recur?" /></label>
        <button onClick={() => patch.mutate({
          root_cause: rcaVal || null, lessons_learnt: lessonsVal || null,
          corrective_action: correctiveVal || null, resolution: resolutionVal || null })}
          className="btn btn-primary mt-3">Save RCA</button>
        <p className="mt-2 text-caption text-txt3">An incident can't be marked <b>Closed</b> until it has a root cause.</p>
      </Card>
      <IncidentTimeline id={id} events={inc.events ?? []} />
      <AttachEvidenceCard base={`/incidents/${id}`} invalidateKey={["incident", id]} rows={inc.evidence ?? []} />
      {inc.owner_name && <div className="text-label text-txt2">Owner · <b>{inc.owner_name}</b></div>}
      <button onClick={() => del.mutate()} className="mt-2 text-label text-txt3 hover:text-bad">Delete this incident</button>
    </Drawer>
  );
}

export function IncidentsTab() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [openId, setOpenId] = useDrawerRoute("/incidents");
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data, isLoading } = useRegisterList<Incident>("incidents", "/incidents", q);
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const columns: Column<Incident>[] = [
    { key: "ref", label: "Ref", sortValue: (i) => i.reference,
      render: (i) => <span className="font-mono text-txt3">{i.reference ?? "—"}</span> },
    { key: "title", label: "Incident", sortValue: (i) => i.title.toLowerCase(),
      render: (i) => <span className="font-medium">{i.title}</span> },
    // P5-S4: the column the `incident_category` vocabulary finally has to land in.
    { key: "cat", label: "Category", sortValue: (i) => i.category,
      render: (i) => <span className="text-txt2">{i.category ?? "—"}</span> },
    { key: "sev", label: "Severity", sortValue: (i) => i.severity,
      render: (i) => i.severity ? <Pill tone={CRIT_TONE[i.severity]}>{cap(i.severity)}</Pill> : <span className="text-txt3">—</span> },
    { key: "det", label: "Detected", sortValue: (i) => i.detected_at,
      render: (i) => <span className="text-txt2">{i.detected_at?.slice(0, 10) ?? "—"}</span> },
    { key: "owner", label: "Owner", sortValue: (i) => i.owner_name,
      render: (i) => <span className="text-txt2">{i.owner_name ?? "—"}</span> },
    { key: "status", label: "Status", sortValue: (i) => i.status,
      render: (i) => <Pill tone={INC_TONE[i.status] ?? "na"}>{nice(i.status)}</Pill> },
  ];
  return (
    <>
      <div className="mb-3 flex justify-end">{can("incidents", "add") && (
          <>
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New incident</button>
          </>
        )}</div>
      {rows.length === 0 && !q ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
          No incidents logged. Banks ask for the <b>RCA</b>, not the ticket — record what happened, the root cause, and the lessons learnt.
        </div>
      ) : (
        <DataTable rows={rows} getId={(i) => i.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search incidents…"
          onRowClick={(i) => setOpenId(i.id)}
          canDelete={can("incidents", "delete")}
          onDeleteOne={(id) => api.delete(`/incidents/${id}`).then(() => {
            qc.invalidateQueries({ queryKey: ["incidents"] }); })}
          noMatchMessage={`No incidents match "${q}".`} />
      )}
      {adding && <NewIncidentModal onClose={() => setAdding(false)} />}
      {importing && (
        <BulkImportModal register="incidents" onClose={() => setImporting(false)} />
      )}
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
