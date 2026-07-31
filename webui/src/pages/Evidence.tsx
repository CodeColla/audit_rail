import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, errText, get } from "../lib/api";
import { inputCls, Loading, Modal, PageHead, Pill, Table, Td } from "../lib/ui";
import { useCan } from "../lib/auth";
import { useDebounced } from "../lib/useDebounced";

type Ev = {
  id: string; title: string; evidence_type: string; issued_at: string | null; valid_until: string | null;
  status: string; linked_controls: number; medium: string;
  original_name: string | null; size_bytes: number | null;
};

const TYPES = ["certificate", "report", "policy_doc", "register", "screenshot", "insurance", "other"];

/** Strip only the extension, matching the server's `Path(...).stem` default. */
const stem = (n: string) => n.replace(/\.[^./\\]+$/, "") || n;

type Slot = { file: File; title: string; state: "idle" | "busy" | "ok" | "failed"; error?: string };

/**
 * Upload several artifacts in one pass.
 *
 * Deliberately N sequential requests against the existing single-file endpoint rather than
 * a multi-file one. `upload_evidence` reads the whole body into memory *before* it checks
 * `max_upload_mb`, so a ten-file multipart POST is a quarter of a gigabyte resident before
 * the server is allowed to object. One request per file keeps that ceiling where it was,
 * and gives each file its own success or failure instead of an all-or-nothing envelope this
 * API has no precedent for.
 */
function UploadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [slots, setSlots] = useState<Slot[]>([]);
  const [type, setType] = useState("certificate");
  const [issued, setIssued] = useState(""); const [valid, setValid] = useState("");
  const [running, setRunning] = useState(false);
  const [uploaded, setUploaded] = useState(0);   // live counter for the button label

  const add = (files: FileList | null) => {
    if (!files) return;
    setSlots((s) => [...s, ...Array.from(files).map((file) => ({
      file, title: stem(file.name), state: "idle" as const }))]);
  };
  const patch = (i: number, next: Partial<Slot>) =>
    setSlots((s) => s.map((x, n) => (n === i ? { ...x, ...next } : x)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    // `slots` is the render-time snapshot. setState during the loop does NOT write back
    // into it, so every decision after the loop has to come from `outcome`, not `slots` —
    // reading the stale array here is how "did the whole batch succeed?" silently answers
    // no forever and the modal never closes.
    const batch = slots;
    if (!batch.some((s) => s.state === "idle" || s.state === "failed")) return;
    setRunning(true);
    const outcome = batch.map((s) => s.state === "ok");
    for (let i = 0; i < batch.length; i++) {
      if (outcome[i]) continue;
      patch(i, { state: "busy", error: undefined });
      try {
        const fd = new FormData();
        if (batch[i].title.trim()) fd.append("title", batch[i].title.trim());
        fd.append("evidence_type", type);
        if (issued) fd.append("issued_at", issued);
        if (valid) fd.append("valid_until", valid);
        fd.append("file", batch[i].file);
        await api.post("/evidence", fd);
        outcome[i] = true;
        patch(i, { state: "ok" });
        setUploaded((n) => n + 1);
      } catch (err: any) {
        patch(i, { state: "failed", error: errText(err, "Upload failed.") });
      }
    }
    setRunning(false);
    if (outcome.some(Boolean)) qc.invalidateQueries({ queryKey: ["evidence"] });  // once, not per file
    if (outcome.every(Boolean)) { setSlots([]); setUploaded(0); onClose(); }
  }

  const done = slots.filter((s) => s.state === "ok").length;
  const pending = slots.some((s) => s.state === "idle" || s.state === "failed");

  return (
    <Modal open={open} onClose={onClose} title="Upload evidence" size="lg">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-3">
          <label className="text-[13px] font-medium">Type
            <select value={type} onChange={(e) => setType(e.target.value)} className={inputCls + " mt-1 capitalize"}>
              {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </select>
          </label>
          <label className="text-[13px] font-medium">Issued
            <input type="date" value={issued} onChange={(e) => setIssued(e.target.value)} className={inputCls + " mt-1"} />
          </label>
          <label className="text-[13px] font-medium">Valid until
            <input type="date" value={valid} onChange={(e) => setValid(e.target.value)} className={inputCls + " mt-1"} />
          </label>
        </div>
        <p className="text-[11.5px] text-txt3">Type and dates apply to every file in this batch — edit any of them afterwards on its own page.</p>

        <label className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed border-hair bg-canvas px-4 py-6 text-center">
          <span className="text-[13px] font-medium">Choose files</span>
          <span className="text-[11.5px] text-txt3">Several at once. Each is named after its file — rename below.</span>
          <input type="file" multiple className="hidden"
            onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </label>

        {slots.length > 0 && (
          <div className="max-h-64 overflow-y-auto rounded-md border border-bd">
            {slots.map((s, i) => (
              <div key={i} className="flex items-center gap-2 border-b border-bd px-2.5 py-2 last:border-b-0">
                <span className="w-5 shrink-0 text-center text-[12px]">
                  {s.state === "ok" ? <span className="text-ok">✓</span>
                    : s.state === "failed" ? <span className="text-bad">✕</span>
                    : s.state === "busy" ? <span className="text-txt3">…</span> : ""}
                </span>
                <div className="min-w-0 flex-1">
                  <input value={s.title} disabled={s.state === "ok" || s.state === "busy"}
                    onChange={(e) => patch(i, { title: e.target.value })}
                    className={inputCls + " py-1 text-[12.5px] disabled:opacity-60"} />
                  <div className="mt-0.5 truncate text-[11px] text-txt3" title={s.file.name}>
                    {s.file.name}
                    {s.error && <span className="text-bad"> · {s.error}</span>}
                  </div>
                </div>
                {s.state !== "ok" && s.state !== "busy" && (
                  <button type="button" onClick={() => setSlots((x) => x.filter((_, n) => n !== i))}
                    className="shrink-0 text-txt3 hover:text-bad">✕</button>)}
              </div>
            ))}
          </div>
        )}

        {done > 0 && pending && (
          <div className="rounded-md bg-warn-bg px-3 py-2 text-[12.5px] text-warn">
            {done} of {slots.length} uploaded. The ones marked ✓ are saved — retry the rest or remove them.
          </div>)}

        <button disabled={!pending || running} className="btn btn-primary justify-center disabled:opacity-50">
          {running ? `Uploading ${Math.min(uploaded + 1, slots.length)} of ${slots.length}…`
            : done > 0 ? "Retry the rest"
            : slots.length > 1 ? `Upload ${slots.length} files` : "Upload"}
        </button>
      </form>
    </Modal>
  );
}

export default function Evidence() {
  const qc = useQueryClient();
  const [modal, setModal] = useState(false);
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  const { data, isLoading } = useQuery({
    // The `q` is part of the key: six components share this cache, and one of them writing
    // a FILTERED result under a bare ["evidence"] would silently truncate the other five.
    queryKey: ["evidence", dq],
    queryFn: () => get<Ev[]>(`/evidence?${new URLSearchParams(dq ? { q: dq } : {})}`),
    placeholderData: keepPreviousData,
  });
  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/evidence/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence"] }),
    onError: (e: any) => setDelErr(errText(e, "Could not delete.")),
  });
  const [delErr, setDelErr] = useState("");
  const can = useCan();
  const canDelete = can("evidence", "delete");
  const nav = useNavigate();
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Evidence vault" title="Evidence"
        lead="Typed, dated artifacts linked to controls. Banks ask for recent proof — freshness is tracked here."
        action={can("evidence", "add")
          ? <button onClick={() => setModal(true)} className="btn btn-primary">＋ Upload evidence</button>
          : undefined} />
      <div className="mb-3">
        <input value={term} onChange={(e) => setTerm(e.target.value)}
          placeholder="Search titles and notes…" className={inputCls + " max-w-sm"} />
      </div>
      {delErr && <div className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{delErr}</div>}
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          {dq ? `Nothing in the vault matches “${dq}”.` : "No evidence yet — upload your first artifact."}
        </div>
      ) : (
        <Table head={["Artifact", "Type", "Issued", "Valid until", "Links", "Status", ""]}>
          {rows.map((e) => (
            <tr key={e.id} className="hover:bg-canvas">
              <Td className="font-medium">
                <button onClick={() => nav(`/evidence/view/${e.id}`)}
                  className="text-left hover:text-accent">{e.title}</button>
                {e.medium === "LINK" && <span className="ml-1.5 text-[10px] uppercase tracking-wide text-txt3">link</span>}
              </Td>
              <Td><span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] capitalize text-txt2">{e.evidence_type.replace(/_/g, " ")}</span></Td>
              <Td className="font-mono text-txt2">{e.issued_at ?? "—"}</Td>
              <Td className="font-mono text-txt2">{e.valid_until ?? "—"}</Td>
              <Td className="tnum">{e.linked_controls} controls</Td>
              <Td><Pill tone={e.status}>{e.status}</Pill></Td>
              <Td>{canDelete && (
                <button onClick={() => { if (confirm(
                    `Delete "${e.title}"?\n\nThe stored file goes with it, and it is detached from ` +
                    `every control, risk, incident and audit answer that cites it. This cannot be undone.`))
                    del.mutate(e.id); }}
                  className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad" title="Delete">
                  <Trash2 size={14} />
                </button>)}</Td>
            </tr>
          ))}
        </Table>
      )}
      <UploadModal open={modal} onClose={() => setModal(false)} />
    </>
  );
}
