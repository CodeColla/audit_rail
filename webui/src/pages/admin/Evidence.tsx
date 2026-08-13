import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { api, errText, get } from "../../lib/api";
import { inputCls, Loading, Modal, PageHead, Pill } from "../../lib/ui";
import { useCan } from "../../lib/auth";
import { Thumb } from "../../components/AttachmentLink";
import { FilePreview } from "../../components/FilePreview";
import { DataTable, Column } from "../../components/DataTable";
import { MAX_UPLOAD_MB, stem } from "../../lib/evidence";
import { LookupSelect } from "../registers/Registers";

type Ev = {
  id: string; title: string; evidence_type: string; issued_at: string | null; valid_until: string | null;
  status: string; linked_controls: number; medium: string;
  original_name: string | null; size_bytes: number | null; mime_type: string | null;
};




const humanSize = (bytes: number) => bytes < 1_000_000
  ? `${Math.round(bytes / 1000)} kB` : `${(bytes / 1_000_000).toFixed(1)} MB`;

type Slot = { id: string; file: File; title: string; state: "idle" | "busy" | "ok" | "failed"; error?: string };

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
  const [dragOver, setDragOver] = useState(false);

  const add = (files: FileList | null) => {
    if (!files) return;
    // Snapshot to a plain array HERE, synchronously — not inside the setSlots updater.
    // `e.target.files` is a live FileList tied to the input element; the input's onChange
    // resets `value = ""` right after calling this, which the browser can settle before
    // React gets around to invoking a lazy updater (confirmed by instrumenting: on every
    // selection after the first, `files.length` had already dropped to 0 by the time the
    // updater ran). That's why only ever the first file in a modal session ever appeared —
    // every later selection silently added nothing.
    const picked = Array.from(files).map((file) => ({
      id: crypto.randomUUID(), file, title: stem(file.name), state: "idle" as const }));
    setSlots((s) => [...s, ...picked]);
  };
  // Keyed on the slot's stable id, not its array position — see the `key={s.id}` note
  // below for why an index would silently corrupt this list once a row is removed.
  const patch = (id: string, next: Partial<Slot>) =>
    setSlots((s) => s.map((x) => (x.id === id ? { ...x, ...next } : x)));

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
      const id = batch[i].id;
      // Check the size cap BEFORE spending a round trip — the server would 413 on the same
      // file anyway, but only after fully reading it into memory first.
      if (batch[i].file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        patch(id, { state: "failed", error: `exceeds the ${MAX_UPLOAD_MB} MB limit` });
        continue;
      }
      patch(id, { state: "busy", error: undefined });
      try {
        const fd = new FormData();
        if (batch[i].title.trim()) fd.append("title", batch[i].title.trim());
        fd.append("evidence_type", type);
        if (issued) fd.append("issued_at", issued);
        if (valid) fd.append("valid_until", valid);
        fd.append("file", batch[i].file);
        await api.post("/evidence", fd);
        outcome[i] = true;
        patch(id, { state: "ok" });
        setUploaded((n) => n + 1);
      } catch (err: any) {
        patch(id, { state: "failed", error: errText(err, "Upload failed.") });
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
          <label className="text-sm font-medium">Type
            {/* P5-S6: was a hardcoded array duplicated in two files. Now the
                `evidence_type` vocabulary, editable in Masters. */}
            <LookupSelect kind="evidence_type" value={type} onChange={setType} />
          </label>
          <label className="text-sm font-medium">Issued
            <input type="date" value={issued} onChange={(e) => setIssued(e.target.value)} className={inputCls + " mt-1"} />
          </label>
          <label className="text-sm font-medium">Valid until
            <input type="date" value={valid} onChange={(e) => setValid(e.target.value)} className={inputCls + " mt-1"} />
          </label>
        </div>
        <p className="text-caption text-txt3">Type and dates apply to every file in this batch — edit any of them afterwards on its own page.</p>

        <label
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault(); setDragOver(false);
            // No `e.target.value` reset to worry about here — a drop doesn't touch the
            // hidden input's own selection state at all, so none of the add() race applies.
            add(e.dataTransfer.files);
          }}
        >
          <div className={
            "flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed px-4 py-6 text-center " +
            (dragOver ? "border-accent bg-[rgba(249,115,22,0.06)]" : "border-hair bg-canvas")
          }>
            <span className="text-sm font-medium">{dragOver ? "Drop to add" : "Choose files"}</span>
            <span className="text-caption text-txt3">Several at once, or drag them in — each is named after its file.</span>
            <input type="file" multiple className="hidden"
              onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          </div>
        </label>

        {slots.length > 0 && (
          <div className="max-h-64 overflow-y-auto rounded-md border border-bd">
            {slots.map((s) => {
              const oversize = s.file.size > MAX_UPLOAD_MB * 1024 * 1024;
              return (
              // Keyed on the slot's own id, not its position in the array. An index key
              // here made React reuse this row's DOM node — including the CONTROLLED
              // title <input> — for whatever slot next occupied that position after a
              // removal, so a deleted row's neighbour would render with the wrong title
              // against the wrong filename.
              <div key={s.id} className="flex items-center gap-2 border-b border-bd px-2.5 py-2 last:border-b-0">
                <span className="w-5 shrink-0 text-center text-label">
                  {s.state === "ok" ? <span className="text-ok">✓</span>
                    : s.state === "failed" ? <span className="text-bad">✕</span>
                    : s.state === "busy" ? <span className="text-txt3">…</span> : ""}
                </span>
                <div className="min-w-0 flex-1">
                  <input value={s.title} disabled={s.state === "ok" || s.state === "busy"}
                    onChange={(e) => patch(s.id, { title: e.target.value })}
                    className={inputCls + " py-1 text-label disabled:opacity-60"} />
                  <div className="mt-0.5 truncate text-caption text-txt3" title={s.file.name}>
                    {s.file.name} · {humanSize(s.file.size)}
                    {oversize && s.state === "idle" && (
                      <span className="text-bad"> · exceeds the {MAX_UPLOAD_MB} MB limit</span>)}
                    {s.error && <span className="text-bad"> · {s.error}</span>}
                  </div>
                </div>
                {s.state !== "ok" && s.state !== "busy" && (
                  <button type="button" onClick={() => setSlots((x) => x.filter((r) => r.id !== s.id))}
                    className="shrink-0 text-txt3 hover:text-bad">✕</button>)}
              </div>
              );
            })}
          </div>
        )}

        {done > 0 && pending && (
          <div className="rounded-md bg-warn-bg px-3 py-2 text-label text-warn">
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

/** The list row's thumbnail, as its own click target — separate from the title, which
 * still navigates to the detail page. Lets you preview without leaving the vault, without
 * losing the existing way to reach an artifact's full page. */
function QuickPreview({ id, title, mimeType }: { id: string; title: string; mimeType: string | null }) {
  const [open, setOpen] = useState(false);
  const url = `/evidence/${id}/file`;
  const isImage = (mimeType ?? "").startsWith("image/") && !/heic|heif/.test(mimeType ?? "");
  return (
    <>
      <button onClick={(e) => { e.stopPropagation(); setOpen(true); }} title={`Preview ${title}`}>
        <Thumb url={url} isImage={isImage} mimeType={mimeType} />
      </button>
      {open && (
        <Modal open onClose={() => setOpen(false)} title={title} size="lg">
          <FilePreview url={url} name={title} />
        </Modal>
      )}
    </>
  );
}

export default function Evidence() {
  const qc = useQueryClient();
  const [modal, setModal] = useState(false);
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    // The `q` is part of the key: six components share this cache, and one of them writing
    // a FILTERED result under a bare ["evidence"] would silently truncate the other five.
    queryKey: ["evidence", q],
    queryFn: () => get<Ev[]>(`/evidence?${new URLSearchParams(q ? { q } : {})}`),
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

  const columns: Column<Ev>[] = [
    {
      key: "title", label: "Artifact", sortValue: (e) => e.title.toLowerCase(),
      render: (e) => (
        <div className="flex items-center gap-1.5">
          {/* The title still navigates to the detail page — that stays the one place to
              edit title/type/dates and manage control links. The thumbnail is a SEPARATE
              quick-preview trigger, so previewing an artifact no longer requires leaving
              the list, without losing the existing way to reach its full page. */}
          {e.medium !== "LINK" && <QuickPreview id={e.id} title={e.title} mimeType={e.mime_type} />}
          <button onClick={() => nav(`/evidence/view/${e.id}`)}
            className="text-left font-medium hover:text-accent">{e.title}</button>
          {e.medium === "LINK" && <span className="text-micro uppercase tracking-wide text-txt3">link</span>}
        </div>
      ),
    },
    {
      key: "type", label: "Type", sortValue: (e) => e.evidence_type,
      render: (e) => <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption capitalize text-txt2">{e.evidence_type.replace(/_/g, " ")}</span>,
    },
    {
      key: "issued", label: "Issued", sortValue: (e) => e.issued_at,
      render: (e) => <span className="font-mono text-txt2">{e.issued_at ?? "—"}</span>,
    },
    {
      key: "valid", label: "Valid until", sortValue: (e) => e.valid_until,
      render: (e) => <span className="font-mono text-txt2">{e.valid_until ?? "—"}</span>,
    },
    {
      key: "links", label: "Links", sortValue: (e) => e.linked_controls,
      render: (e) => <span className="tnum">{e.linked_controls} controls</span>,
    },
    {
      key: "status", label: "Status", sortValue: (e) => e.status,
      render: (e) => <Pill tone={e.status}>{e.status}</Pill>,
    },
    {
      // No sortValue — an actions column, not data.
      key: "actions", label: "",
      render: (e) => canDelete && (
        <button onClick={() => { if (confirm(
            `Delete "${e.title}"?\n\nThe stored file goes with it, and it is detached from ` +
            `every control, risk, incident and audit answer that cites it. This cannot be undone.`))
            del.mutate(e.id); }}
          className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad" title="Delete">
          <Trash2 size={14} />
        </button>),
    },
  ];

  return (
    <>
      <PageHead eyebrow="Evidence vault" title="Evidence"
        lead="Typed, dated artifacts linked to controls. Banks ask for recent proof — freshness is tracked here."
        action={can("evidence", "add")
          ? <button onClick={() => setModal(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> Upload evidence</button>
          : undefined} />
      {delErr && <div className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{delErr}</div>}
      <DataTable
        rows={rows} getId={(e) => e.id} columns={columns}
        onSearch={setQ} searchPlaceholder="Search titles and notes…"
        canDelete={canDelete} onDeleteOne={(id) => api.delete(`/evidence/${id}`).then(() => {
          qc.invalidateQueries({ queryKey: ["evidence"] });
        })}
        emptyMessage="No evidence yet — upload your first artifact."
        noMatchMessage={`Nothing in the vault matches "${q}".`}
      />
      <UploadModal open={modal} onClose={() => setModal(false)} />
    </>
  );
}
