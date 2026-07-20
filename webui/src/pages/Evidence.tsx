import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, get } from "../lib/api";
import { inputCls, Loading, Modal, PageHead, Pill, Table, Td } from "../lib/ui";
import { useAuth } from "../lib/auth";

type Ev = {
  id: string; title: string; evidence_type: string; issued_at: string | null; valid_until: string | null;
  status: string; linked_controls: number;
};

const TYPES = ["certificate", "report", "policy_doc", "register", "screenshot", "insurance", "other"];

function UploadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(""); const [type, setType] = useState("certificate");
  const [issued, setIssued] = useState(""); const [valid, setValid] = useState("");
  const [file, setFile] = useState<File | null>(null); const [err, setErr] = useState("");

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("title", title); fd.append("evidence_type", type);
      if (issued) fd.append("issued_at", issued);
      if (valid) fd.append("valid_until", valid);
      fd.append("file", file!);
      return api.post("/evidence", fd);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["evidence"] }); onClose(); },
    onError: () => setErr("Upload failed."),
  });

  function submit(e: FormEvent) { e.preventDefault(); if (title && file) upload.mutate(); }

  return (
    <Modal open={open} onClose={onClose} title="Upload evidence">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required className={inputCls + " mt-1"} placeholder="ISO 27001 Certificate" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Type
            <select value={type} onChange={(e) => setType(e.target.value)} className={inputCls + " mt-1 capitalize"}>
              {TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </select>
          </label>
          <div />
          <label className="text-[13px] font-medium">Issued
            <input type="date" value={issued} onChange={(e) => setIssued(e.target.value)} className={inputCls + " mt-1"} />
          </label>
          <label className="text-[13px] font-medium">Valid until
            <input type="date" value={valid} onChange={(e) => setValid(e.target.value)} className={inputCls + " mt-1"} />
          </label>
        </div>
        <label className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed border-hair bg-canvas px-4 py-6 text-center">
          <span className="text-[13px] font-medium">{file ? file.name : "Choose a file"}</span>
          <input type="file" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!title || !file || upload.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {upload.isPending ? "Uploading…" : "Upload"}
        </button>
      </form>
    </Modal>
  );
}

export default function Evidence() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [modal, setModal] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["evidence"], queryFn: () => get<Ev[]>("/evidence") });
  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/evidence/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence"] }),
  });
  const canDelete = user?.role === "admin" || user?.role === "manager";
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Evidence vault" title="Evidence"
        lead="Typed, dated artifacts linked to controls. Banks ask for recent proof — freshness is tracked here."
        action={<button onClick={() => setModal(true)} className="btn btn-primary">＋ Upload evidence</button>} />
      {rows.length === 0 ? <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">No evidence yet — upload your first artifact.</div> : (
        <Table head={["Artifact", "Type", "Issued", "Valid until", "Links", "Status", ""]}>
          {rows.map((e) => (
            <tr key={e.id} className="hover:bg-canvas">
              <Td className="font-medium">
                <a href={`/api/evidence/${e.id}/file`} target="_blank" className="hover:text-accent">{e.title}</a>
              </Td>
              <Td><span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] capitalize text-txt2">{e.evidence_type.replace(/_/g, " ")}</span></Td>
              <Td className="font-mono text-txt2">{e.issued_at ?? "—"}</Td>
              <Td className="font-mono text-txt2">{e.valid_until ?? "—"}</Td>
              <Td className="tnum">{e.linked_controls} controls</Td>
              <Td><Pill tone={e.status}>{e.status}</Pill></Td>
              <Td>{canDelete && (
                <button onClick={() => del.mutate(e.id)} className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad" title="Delete">
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
