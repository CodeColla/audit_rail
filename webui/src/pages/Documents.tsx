import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, get } from "../lib/api";
import { cn, inputCls, Loading, Modal, PageHead, Pill, Table, Td } from "../lib/ui";

type Doc = {
  id: string; title: string; document_type: string; classification: string;
  owner_name: string; published_version: string | null; latest_version: string | null;
  latest_status: string | null; next_review_at: string | null; review_status: string;
};
type Person = { id: string; full_name: string };

const TYPE_CHIPS: { label: string; type: string | null }[] = [
  { label: "All", type: null }, { label: "Policies", type: "POLICY" },
  { label: "Procedures", type: "PROCEDURE" }, { label: "Plans", type: "PLAN" },
  { label: "Governance", type: "GOVERNANCE" },
];
const TYPES = ["POLICY", "PROCEDURE", "PLAN", "GOVERNANCE", "STANDARD", "RECORD", "REPORT"];
const CLASSES = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"];

/** A document's live state: the newest version's status, shown as a pill. */
function StatusCell({ d }: { d: Doc }) {
  const s = d.latest_status;
  if (!s) return <span className="text-txt3">—</span>;
  const tone = s === "PUBLISHED" ? "ok" : s === "PENDING_APPROVAL" ? "warn" : "na";
  const label = s === "PENDING_APPROVAL" ? "In approval" : s.charAt(0) + s.slice(1).toLowerCase();
  return <span className="flex items-center gap-2">
    <Pill tone={tone}>{label}</Pill>
    {d.latest_version && <span className="font-mono text-[11px] text-txt3">v{d.latest_version}</span>}
  </span>;
}

function NewDocModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const [f, setF] = useState({ title: "", document_type: "POLICY", classification: "INTERNAL",
    owner_person_id: "", review_cadence_months: "12" });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const create = useMutation({
    mutationFn: () => api.post("/documents", {
      ...f, review_cadence_months: f.review_cadence_months ? +f.review_cadence_months : null }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["documents"] }); nav(`/documents/${r.data.id}`); },
    onError: (e: any) => setErr(e?.response?.data?.detail ?? "Could not create."),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title && f.owner_person_id) create.mutate(); };

  return (
    <Modal open onClose={onClose} title="New document">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title *
          <input required value={f.title} onChange={set("title")} className={inputCls + " mt-1"}
            placeholder="Information Security Policy" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Type
            <select value={f.document_type} onChange={set("document_type")} className={inputCls + " mt-1 capitalize"}>
              {TYPES.map((t) => <option key={t} value={t}>{t.charAt(0) + t.slice(1).toLowerCase()}</option>)}
            </select>
          </label>
          <label className="text-[13px] font-medium">Classification
            <select value={f.classification} onChange={set("classification")} className={inputCls + " mt-1 capitalize"}>
              {CLASSES.map((c) => <option key={c} value={c}>{c.charAt(0) + c.slice(1).toLowerCase()}</option>)}
            </select>
          </label>
          <label className="text-[13px] font-medium">Owner *
            <select required value={f.owner_person_id} onChange={set("owner_person_id")} className={inputCls + " mt-1"}>
              <option value="">— pick a person —</option>
              {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
            </select>
          </label>
          <label className="text-[13px] font-medium">Review every (months)
            <input type="number" value={f.review_cadence_months} onChange={set("review_cadence_months")} className={inputCls + " mt-1"} />
          </label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <p className="rounded-md bg-canvas px-3 py-2 text-[11.5px] text-txt2">
          Creates a <b>v1.0 draft</b> to write into. It won't publish until it's approved.
        </p>
        <button disabled={!f.title || !f.owner_person_id || create.isPending}
          className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create & write"}
        </button>
      </form>
    </Modal>
  );
}

export default function Documents() {
  const [params, setParams] = useSearchParams();
  const typeParam = params.get("type");
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ["documents", typeParam],
    queryFn: () => get<Doc[]>(`/documents${typeParam ? `?document_type=${typeParam}` : ""}`),
  });
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Program · Documents" title="Documents"
        lead="Policies, procedures and plans — authored, approved by a quorum, published as controlled PDFs. (Registers will render themselves here too, from Sprint 5.)"
        action={<button onClick={() => setAdding(true)} className="btn btn-primary">＋ New document</button>} />

      <div className="mb-4 flex flex-wrap gap-2">
        {TYPE_CHIPS.map((c) => (
          <button key={c.label}
            onClick={() => setParams(c.type ? { type: c.type } : {})}
            className={cn("rounded-full border px-3 py-1.5 text-[12.5px] font-medium",
              (typeParam ?? null) === c.type ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
            {c.label}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center">
          <h3 className="text-[15px] font-semibold">No documents here yet</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-[13px] text-txt2">
            This is where "Please share the policy" gets answered. Write your Information Security
            Policy, route it to approvers, and publish a controlled v1.0 — no more Word file on a
            share drive.
          </p>
          <button onClick={() => setAdding(true)} className="btn btn-primary mt-4">＋ New document</button>
        </div>
      ) : (
        <Table head={["Title", "Type", "Classification", "Status", "Owner", "Next review"]}>
          {rows.map((d) => (
            <tr key={d.id} className="cursor-pointer hover:bg-canvas" onClick={() => nav(`/documents/${d.id}`)}>
              <Td><div className="font-medium">{d.title}</div>
                {d.published_version && <div className="font-mono text-[11px] text-txt3">published v{d.published_version}</div>}</Td>
              <Td className="capitalize text-txt2">{d.document_type.toLowerCase()}</Td>
              <Td><span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] capitalize text-txt2">{d.classification.toLowerCase()}</span></Td>
              <Td><StatusCell d={d} /></Td>
              <Td className="text-txt2">{d.owner_name}</Td>
              <Td>{d.next_review_at
                ? <Pill tone={d.review_status === "overdue" ? "bad" : d.review_status === "due_soon" ? "warn" : "ok"}>
                    {d.next_review_at.slice(0, 10)}</Pill>
                : <span className="text-txt3">—</span>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewDocModal onClose={() => setAdding(false)} />}
      <p className="mt-6 text-[11.5px] text-txt3">
        Looking for the old Policies page? It's here now — <Link to="/documents?type=POLICY" className="underline">Documents → Policies</Link>.
      </p>
    </>
  );
}
