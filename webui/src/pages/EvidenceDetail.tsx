import { FormEvent, useState } from "react";
import { Plus } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePreview } from "../components/FilePreview";
import { api, downloadFile, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { Card, cn, inputCls, Loading, Pill } from "../lib/ui";
import { useDebounced } from "../lib/useDebounced";

/** The 7 the upload form offers. `evidence_type` has no CHECK, so this is a convention. */
import { LookupSelect } from "./Registers";

type LinkedControl = { id: string; code: string; statement: string };
type EvidenceDetailRow = {
  id: string; title: string; evidence_type: string; medium: string; state: string;
  issued_at: string | null; valid_until: string | null; notes: string | null;
  external_url: string | null; file_id: string | null; status: string;
  original_name: string | null; mime_type: string | null; size_bytes: number | null;
  linked_controls: LinkedControl[];
};
type ControlRow = { id: string; code: string; statement: string };

/** 1 kB = 1000 B, matching what every OS file manager shows a user. */
function humanSize(n: number | null): string {
  if (n === null || n === undefined) return "—";
  if (n < 1000) return `${n} B`;
  const units = ["kB", "MB", "GB"];
  let v = n / 1000, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function EditForm({ ev, onDone }: { ev: EvidenceDetailRow; onDone: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({
    title: ev.title, evidence_type: ev.evidence_type,
    issued_at: ev.issued_at ?? "", valid_until: ev.valid_until ?? "",
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const save = useMutation({
    mutationFn: () => api.patch(`/evidence/${ev.id}`, f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evidence"] });          // prefix: every list + picker
      qc.invalidateQueries({ queryKey: ["evidence-detail", ev.id] });
      onDone();
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title.trim()) save.mutate(); };
  return (
    <Card>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} /></label>
        <div className="grid grid-cols-3 gap-3">
          <label className="text-sm font-medium">Type
            {/* LookupSelect keeps an off-list value selectable, which matters here: live
                data holds both `report` and `REPORT` from the free-text era. */}
            <LookupSelect kind="evidence_type" value={f.evidence_type}
              onChange={set("evidence_type")} /></label>
          <label className="text-sm font-medium">Issued
            <input type="date" value={f.issued_at} onChange={(e) => set("issued_at")(e.target.value)}
              className={inputCls + " mt-1"} /></label>
          <label className="text-sm font-medium">Valid until
            <input type="date" value={f.valid_until} onChange={(e) => set("valid_until")(e.target.value)}
              className={inputCls + " mt-1"} /></label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <div className="flex gap-2">
          <button disabled={!f.title.trim() || save.isPending} className="btn btn-primary disabled:opacity-50">
            {save.isPending ? "Saving…" : "Save"}</button>
          <button type="button" onClick={onDone} className="btn">Cancel</button>
        </div>
      </form>
    </Card>
  );
}

/**
 * Link this artifact to controls, from the evidence side. The same `evidence_controls`
 * rows are writable from the control page, so both caches must be invalidated or one
 * screen shows a link the other does not.
 */
function LinkControls({ ev, canEdit }: { ev: EvidenceDetailRow; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  const [err, setErr] = useState("");
  const list = useQuery({
    queryKey: ["controls-lite", dq], enabled: picking,
    queryFn: () => get<ControlRow[]>(`/library/controls?${new URLSearchParams(dq ? { q: dq } : {})}`),
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["evidence-detail", ev.id] });
    qc.invalidateQueries({ queryKey: ["evidence"] });
    qc.invalidateQueries({ queryKey: ["control"] });   // the control page's own link list
  };
  const link = useMutation({
    mutationFn: (control_id: string) => api.post(`/evidence/${ev.id}/controls`, { control_id }),
    onSuccess: () => { setErr(""); setPicking(false); setTerm(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not link.")),
  });
  const unlink = useMutation({
    mutationFn: (control_id: string) => api.delete(`/evidence/${ev.id}/controls/${control_id}`),
    onSuccess: () => { setErr(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not unlink.")),
  });
  const linked = new Set(ev.linked_controls.map((c) => c.id));
  const pickable = (list.data ?? []).filter((c) => !linked.has(c.id));

  return (
    <Card>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="eyebrow">Controls this proves · {ev.linked_controls.length}</div>
        {canEdit && <button onClick={() => setPicking((s) => !s)}
          className="text-label font-medium text-accent"><Plus size={15} strokeWidth={2.4} /> Link a control</button>}
      </div>
      {ev.linked_controls.length === 0 && (
        <p className="text-label text-txt3">
          Not linked to any control yet — linking is what makes this artifact answer a bank's question.</p>)}
      {ev.linked_controls.map((c) => (
        <div key={c.id} className="flex items-center gap-2.5 border-t border-bd py-2 text-label first:border-t-0">
          <Link to={`/controls/view/${c.id}`} className="shrink-0 font-mono font-semibold text-accent hover:underline">{c.code}</Link>
          <span className="min-w-0 flex-1 truncate text-txt2">{c.statement}</span>
          {canEdit && <button onClick={() => unlink.mutate(c.id)}
            className="shrink-0 text-caption text-bad hover:underline">remove</button>}
        </div>))}
      {err && <div className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
      {picking && (
        <div className="mt-2">
          <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
            placeholder="Search by reference or statement…" className={inputCls} />
          <div className="mt-2 max-h-56 overflow-y-auto rounded-md border border-bd">
            {list.isPending ? (
              <div className="px-3 py-2 text-label text-txt3">Searching…</div>
            ) : pickable.length === 0 ? (
              <div className="px-3 py-2 text-label text-txt3">
                {dq ? "No control matches that." : "Every control is already linked."}</div>
            ) : pickable.map((c) => (
              <button key={c.id} onClick={() => link.mutate(c.id)}
                className="flex w-full items-start gap-2 px-3 py-2 text-left text-label hover:bg-canvas">
                <span className="shrink-0 font-mono font-semibold text-accent">{c.code}</span>
                <span className="min-w-0 flex-1 truncate text-txt2">{c.statement}</span>
              </button>))}
          </div>
        </div>)}
    </Card>
  );
}

export default function EvidenceDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const can = useCan();
  const [editing, setEditing] = useState(false);
  const { data: ev, isLoading, isError } = useQuery({
    queryKey: ["evidence-detail", id], queryFn: () => get<EvidenceDetailRow>(`/evidence/${id}`),
  });
  const del = useMutation({
    mutationFn: () => api.delete(`/evidence/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence"] }); nav("/evidence"); },
    onError: (e: any) => setDelErr(errText(e, "Could not delete.")),
  });
  const [delErr, setDelErr] = useState("");

  if (isLoading) return <Loading />;
  if (isError || !ev) return (
    <>
      <Link to="/evidence" className="text-sm text-txt2 hover:text-ink">← Evidence vault</Link>
      <div className="mt-6 rounded-xl border border-dashed border-bd bg-paper p-10 text-center text-sm text-txt2">
        That artifact no longer exists, or you do not have access to it.
      </div>
    </>
  );

  const canEdit = can("evidence", "edit");
  const isLink = ev.medium === "LINK";

  return (
    <>
      <Link to="/evidence" className="text-sm text-txt2 hover:text-ink">← Evidence vault</Link>
      <div className="mb-1 mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-title font-semibold tracking-[-0.01em]">{ev.title}</h1>
        <Pill tone={ev.status}>{ev.status}</Pill>
        {isLink && <Pill tone="info">External link</Pill>}
      </div>
      <p className="mb-4 flex flex-wrap items-center gap-x-1.5 gap-y-2 text-sm text-txt2">
        <span className="capitalize">{ev.evidence_type.replace(/_/g, " ")}</span>
        {ev.issued_at && <> · issued <span className="font-mono">{ev.issued_at}</span></>}
        {ev.valid_until && <> · valid until <span className="font-mono">{ev.valid_until}</span></>}
        {canEdit && !editing && (
          <button onClick={() => setEditing(true)} className="btn ml-2 py-1.5">Edit details</button>)}
        {ev.file_id && (
          <button onClick={() => downloadFile(`/evidence/${ev.id}/file`, ev.original_name ?? ev.title)}
            className="btn py-1.5">Download</button>)}
        {can("evidence", "delete") && (
          <button onClick={() => { if (confirm(
              `Delete "${ev.title}"?\n\nThe stored file goes with it, and it is detached from every ` +
              `control, risk, incident and audit answer that cites it. This cannot be undone.`))
              del.mutate(); }}
            disabled={del.isPending}
            className="btn py-1.5 text-bad hover:border-bad disabled:opacity-50">Delete</button>)}
      </p>
      {delErr && <div className="mb-4 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{delErr}</div>}

      {editing && <div className="mb-4"><EditForm ev={ev} onDone={() => setEditing(false)} /></div>}

      <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <div className="eyebrow mb-2">The artifact</div>
          {/* Every artifact scripts/seed_demo.py creates is medium='LINK' with no file_id.
              The vault used to render FilePreview for those too, which 404s on a join that
              can never match — so all four demo artifacts showed a failed preview and the
              external_url, the only thing that says where the artifact actually IS, was
              displayed nowhere in the product. */}
          {isLink ? (
            <>
              <p className="mb-2 text-label text-txt2">
                Held outside the vault. audit_rail records that it exists and when it expires;
                the bytes live at:</p>
              <a href={ev.external_url ?? "#"} target="_blank" rel="noopener noreferrer"
                className="break-all text-label font-medium text-accent hover:underline">
                {ev.external_url}</a>
            </>
          ) : (
            <div className="flex flex-col gap-1.5">
              {([["Filename", ev.original_name ?? "—"],
                 ["Type", ev.mime_type ?? "—"],
                 ["Size", humanSize(ev.size_bytes)]] as const).map(([label, value]) => (
                <div key={label}
                  className="flex items-center gap-2 border-t border-bd py-1.5 text-label first:border-t-0">
                  <span className="w-24 shrink-0 text-txt3">{label}</span>
                  <span className="min-w-0 flex-1 truncate font-mono">{value}</span>
                </div>))}
            </div>
          )}
          {ev.notes && <p className="mt-3 border-t border-bd pt-3 text-label text-txt2">{ev.notes}</p>}
        </Card>
        <LinkControls ev={ev} canEdit={canEdit} />
      </div>

      {ev.file_id && (
        <Card className={cn("mb-4")}>
          <div className="eyebrow mb-2">Preview</div>
          <FilePreview url={`/evidence/${ev.id}/file`} name={ev.original_name ?? ev.title} />
        </Card>)}
    </>
  );
}
