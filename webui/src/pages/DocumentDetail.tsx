import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, get } from "../lib/api";
import { Card, cn, inputCls, Loading, Modal, Pill } from "../lib/ui";

type Version = {
  id: string; version_label: string; major: number; minor: number; status: string;
  content: string; changelog: string | null; published_at: string | null; file_id: string | null;
};
type Decision = { approver_person_id: string; full_name: string; state: string; comment: string | null };
type Approval = {
  id: string; threshold_required: number; status: string; approved: number;
  can_publish: boolean; decisions: Decision[];
};
type Detail = {
  id: string; title: string; document_type: string; classification: string;
  owner: { id: string; full_name: string } | null; review_status: string; next_review_at: string | null;
  current_published_version_id: string | null; review_cadence_months: number | null;
  versions: Version[]; open_version: Version | null; approval: Approval | null;
};
type Person = { id: string; full_name: string; effective_state: string };

const Md = ({ children }: { children: string }) => (
  <div className="doc-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children || "*(empty)*"}</ReactMarkdown></div>
);

const statusTone = (s: string) =>
  s === "PUBLISHED" ? "ok" : s === "PENDING_APPROVAL" ? "warn" : s === "SUPERSEDED" ? "na" : "info";

// ─────────────────────────────────────────────────────── editor
function Editor({ docId, version, onDone }:
  { docId: string; version: Version; onDone: () => void }) {
  const qc = useQueryClient();
  const [content, setContent] = useState(version.content);
  const [changelog, setChangelog] = useState(version.changelog ?? "");
  const save = useMutation({
    mutationFn: () => api.patch(`/documents/${docId}/versions/${version.id}`, { content, changelog }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", docId] }); onDone(); },
  });
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <input value={changelog} onChange={(e) => setChangelog(e.target.value)}
          placeholder="Changelog — what changed in this version?" className={inputCls} />
        <button disabled={save.isPending} onClick={() => save.mutate()}
          className="btn btn-primary shrink-0 disabled:opacity-50">{save.isPending ? "Saving…" : "Save draft"}</button>
        <button onClick={onDone} className="btn shrink-0">Done</button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <textarea value={content} onChange={(e) => setContent(e.target.value)}
          className="min-h-[60vh] rounded-xl border border-bd bg-paper p-4 font-mono text-[13px] leading-relaxed outline-none focus:border-accent"
          placeholder="# Heading&#10;&#10;Write the policy in markdown…" />
        <div className="min-h-[60vh] overflow-y-auto rounded-xl border border-bd bg-paper p-5">
          <Md>{content}</Md>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────── diff modal
function DiffModal({ docId, versions, onClose }:
  { docId: string; versions: Version[]; onClose: () => void }) {
  const [from, setFrom] = useState(versions[1]?.id ?? versions[0]?.id);
  const [to, setTo] = useState(versions[0]?.id);
  const diff = useQuery({
    queryKey: ["diff", docId, from, to],
    queryFn: () => get<{ diff: string[]; added: number; removed: number }>(
      `/documents/${docId}/diff?from_version=${from}&to_version=${to}`),
    enabled: !!from && !!to && from !== to,
  });
  const vlabel = (id: string) => versions.find((v) => v.id === id)?.version_label ?? "?";
  return (
    <Modal open onClose={onClose} title="Compare versions">
      <div className="mb-3 flex items-center gap-2 text-[13px]">
        <select value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls}>
          {versions.map((v) => <option key={v.id} value={v.id}>v{v.version_label}</option>)}
        </select>
        <span className="text-txt3">→</span>
        <select value={to} onChange={(e) => setTo(e.target.value)} className={inputCls}>
          {versions.map((v) => <option key={v.id} value={v.id}>v{v.version_label}</option>)}
        </select>
      </div>
      {from === to ? <div className="text-[13px] text-txt3">Pick two different versions.</div>
        : diff.isLoading ? <div className="text-[13px] text-txt3">Loading…</div>
        : (
          <>
            <div className="mb-2 flex gap-3 text-[12px]">
              <span className="text-ok">+{diff.data?.added ?? 0} added</span>
              <span className="text-bad">−{diff.data?.removed ?? 0} removed</span>
              <span className="text-txt3">v{vlabel(from)} → v{vlabel(to)}</span>
            </div>
            <pre className="max-h-[55vh] overflow-auto rounded-md border border-bd bg-canvas p-3 font-mono text-[12px] leading-relaxed">
              {(diff.data?.diff ?? []).map((l, i) => (
                <div key={i} className={cn(
                  l.startsWith("+") && !l.startsWith("+++") && "bg-ok-bg text-ok",
                  l.startsWith("-") && !l.startsWith("---") && "bg-bad-bg text-bad",
                  l.startsWith("@@") && "text-info")}>{l || " "}</div>
              ))}
              {diff.data?.diff.length === 0 && <span className="text-txt3">Identical.</span>}
            </pre>
          </>
        )}
    </Modal>
  );
}

// ─────────────────────────────────────────────────────── submit-for-approval
function SubmitModal({ docId, versionId, onClose }:
  { docId: string; versionId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const active = (people.data ?? []).filter((p) => p.effective_state === "ACTIVE");
  const [picked, setPicked] = useState<string[]>([]);
  const [threshold, setThreshold] = useState(1);
  const [err, setErr] = useState("");
  const submit = useMutation({
    mutationFn: () => api.post(`/documents/${docId}/versions/${versionId}/submit`,
      { threshold_required: threshold, approver_person_ids: picked }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", docId] }); onClose(); },
    onError: (e: any) => setErr(e?.response?.data?.detail ?? "Could not submit."),
  });
  const toggle = (id: string) =>
    setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id]);

  return (
    <Modal open onClose={onClose} title="Request approval">
      <p className="mb-3 text-[12.5px] text-txt2">
        Pick approvers, then set how many must approve — this is the <b>M of N</b> threshold, frozen
        now and unchangeable mid-flight. Departed staff aren't shown.
      </p>
      <div className="max-h-52 overflow-y-auto rounded-md border border-bd">
        {active.map((p) => (
          <label key={p.id} className="flex cursor-pointer items-center gap-2.5 border-b border-bd px-3 py-2 text-[13px] last:border-0 hover:bg-canvas">
            <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
            {p.full_name}
          </label>
        ))}
        {active.length === 0 && <div className="px-3 py-2 text-[12.5px] text-txt3">No active people — add some in People first.</div>}
      </div>
      <div className="mt-3 flex items-center gap-2 text-[13px]">
        <span>Threshold</span>
        <button onClick={() => setThreshold((t) => Math.max(1, t - 1))} className="grid h-7 w-7 place-items-center rounded-md border border-bd">−</button>
        <span className="w-8 text-center font-semibold tnum">{threshold}</span>
        <button onClick={() => setThreshold((t) => Math.min(Math.max(1, picked.length), t + 1))} className="grid h-7 w-7 place-items-center rounded-md border border-bd">+</button>
        <span className="text-txt3">of {picked.length} picked</span>
      </div>
      {err && <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
      <button disabled={picked.length === 0 || threshold > picked.length || submit.isPending}
        onClick={() => submit.mutate()} className="btn btn-primary mt-3 w-full justify-center disabled:opacity-50">
        {submit.isPending ? "Submitting…" : `Request ${threshold} of ${picked.length} approval`}
      </button>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────── approvals tab
function ApprovalsTab({ doc }: { doc: Detail }) {
  const qc = useQueryClient();
  const [submitting, setSubmitting] = useState(false);
  const open = doc.open_version;
  const appr = doc.approval;
  const decide = useMutation({
    mutationFn: (v: { pid: string; state: string }) =>
      api.post(`/documents/approvals/${appr!.id}/decide`,
        { approver_person_id: v.pid, state: v.state }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["document", doc.id] }),
  });
  const publish = useMutation({
    mutationFn: () => api.post(`/documents/${doc.id}/versions/${open!.id}/publish`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["document", doc.id] }),
  });

  if (!open) return <Card><div className="text-[13px] text-txt3">
    No version in progress. Use <b>Edit</b> to start a new version, then request approval here.</div></Card>;

  if (open.status === "DRAFT")
    return (
      <>
        <Card>
          <div className="eyebrow mb-1">Draft v{open.version_label}</div>
          <p className="text-[13px] text-txt2">This draft hasn't been sent for approval yet.
            An approved quorum is required before it can be published — including minor versions.</p>
          <button onClick={() => setSubmitting(true)} className="btn btn-primary mt-3">Request approval</button>
        </Card>
        {submitting && <SubmitModal docId={doc.id} versionId={open.id} onClose={() => setSubmitting(false)} />}
      </>
    );

  // PENDING_APPROVAL with a round
  if (!appr) return <Card><div className="text-[13px] text-txt3">No approval round.</div></Card>;
  const pct = Math.round((appr.approved / appr.threshold_required) * 100);
  return (
    <Card>
      <div className="mb-1 flex items-center justify-between">
        <div className="eyebrow">Approval · v{open.version_label} · {appr.status}</div>
        <Pill tone={appr.can_publish ? "ok" : "warn"}>{appr.approved} of {appr.threshold_required} required</Pill>
      </div>
      <div className="my-3 h-2 overflow-hidden rounded-full bg-bd">
        <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <div className="flex flex-col gap-1">
        {appr.decisions.map((d) => (
          <div key={d.approver_person_id} className="flex items-center gap-3 border-t border-bd py-2 text-[13px] first:border-t-0">
            <span className={cn("grid h-6 w-6 place-items-center rounded-full text-[11px] text-white",
              d.state === "APPROVED" ? "bg-ok" : d.state === "REJECTED" ? "bg-bad" : "bg-txt3")}>
              {d.state === "APPROVED" ? "✓" : d.state === "REJECTED" ? "✕" : "○"}</span>
            <span className="font-medium">{d.full_name}</span>
            <span className="text-txt3">{d.state.toLowerCase()}{d.comment ? ` — "${d.comment}"` : ""}</span>
            {d.state === "PENDING" && (
              <span className="ml-auto flex gap-1.5">
                <button onClick={() => decide.mutate({ pid: d.approver_person_id, state: "APPROVED" })}
                  className="rounded-md border border-ok/40 px-2 py-1 text-[12px] text-ok hover:bg-ok-bg">Approve</button>
                <button onClick={() => decide.mutate({ pid: d.approver_person_id, state: "REJECTED" })}
                  className="rounded-md border border-bd px-2 py-1 text-[12px] text-txt2 hover:border-bad hover:text-bad">Reject</button>
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button disabled={!appr.can_publish || publish.isPending} onClick={() => publish.mutate()}
          className="btn btn-primary disabled:opacity-50">
          {publish.isPending ? "Publishing…" : `Publish v${open.version_label}`}
        </button>
        {!appr.can_publish && <span className="text-[12px] text-txt3">{appr.approved} of {appr.threshold_required} approvals — publish unlocks at {appr.threshold_required}.</span>}
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────── page
export default function DocumentDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"content" | "versions" | "approvals">("content");
  const [editing, setEditing] = useState(false);
  const [diffing, setDiffing] = useState(false);

  const { data: doc, isLoading } = useQuery({ queryKey: ["document", id], queryFn: () => get<Detail>(`/documents/${id}`) });

  const newDraft = useMutation({
    mutationFn: (bump: string) => api.post(`/documents/${id}/versions`, { bump }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", id] }); setEditing(true); },
  });

  const shown = useMemo(() => {
    if (!doc) return null;
    const pub = doc.versions.find((v) => v.id === doc.current_published_version_id);
    return doc.open_version ?? pub ?? doc.versions[0] ?? null;
  }, [doc]);

  if (isLoading || !doc) return <Loading />;

  function onEdit() {
    if (doc!.open_version?.status === "DRAFT") setEditing(true);
    else newDraft.mutate("minor");   // published → start a new minor draft
  }

  return (
    <>
      <Link to="/documents" className="text-[13px] text-txt2 hover:text-ink">← All documents</Link>
      <div className="mb-1 mt-2 flex items-end gap-3">
        <h1 className="text-[22px] font-semibold tracking-[-0.01em]">{doc.title}</h1>
        {shown && <Pill tone={statusTone(shown.status)}>{shown.status === "PENDING_APPROVAL" ? "In approval" : shown.status.charAt(0) + shown.status.slice(1).toLowerCase()}</Pill>}
        <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] capitalize text-txt2">{doc.classification.toLowerCase()}</span>
      </div>
      <p className="mb-3 flex flex-wrap items-center gap-x-1.5 text-[13.5px] text-txt2">
        <span className="capitalize">{doc.document_type.toLowerCase()}</span> · Owner {doc.owner?.full_name ?? "—"}
        {shown && <> · v{shown.version_label}</>}
        {doc.next_review_at && <> · <span className={doc.review_status === "overdue" ? "text-bad" : "text-txt2"}>review {doc.next_review_at.slice(0, 10)}</span></>}
        <button onClick={onEdit} className="btn ml-2 py-1.5">
          {doc.open_version?.status === "DRAFT" ? "Continue editing" : "Edit → new version"}
        </button>
        {doc.current_published_version_id && (
          <a href={`/api/documents/${doc.id}/versions/${doc.current_published_version_id}/render.pdf`}
            target="_blank" className="btn py-1.5">Export PDF ↓</a>)}
      </p>

      {doc.open_version && doc.open_version.status === "DRAFT" && !editing && tab === "content" && (
        <div className="mb-4 rounded-lg border border-warn/40 bg-warn-bg/40 px-4 py-2.5 text-[12.5px] text-warn">
          Draft v{doc.open_version.version_label} in progress —{" "}
          <button onClick={() => setEditing(true)} className="font-semibold underline">continue editing</button>{" "}
          or go to <button onClick={() => setTab("approvals")} className="font-semibold underline">Approvals</button> to request sign-off.
        </div>
      )}

      <div className="mb-5 flex gap-1 border-b border-bd">
        {(["content", "versions", "approvals"] as const).map((tt) => (
          <button key={tt} onClick={() => { setTab(tt); setEditing(false); }}
            className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-[13px] font-medium capitalize",
              tab === tt ? "border-accent text-ink" : "border-transparent text-txt2")}>
            {tt}{tt === "versions" && ` (${doc.versions.length})`}
          </button>
        ))}
      </div>

      {tab === "content" && (editing && doc.open_version
        ? <Editor docId={doc.id} version={doc.open_version} onDone={() => setEditing(false)} />
        : <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_240px]">
            <Card className="min-h-[40vh]"><Md>{shown?.content ?? ""}</Md></Card>
            <div className="space-y-4">
              <Card>
                <div className="eyebrow mb-2">Details</div>
                {[["Type", doc.document_type.toLowerCase()], ["Classification", doc.classification.toLowerCase()],
                  ["Owner", doc.owner?.full_name], ["Review every", doc.review_cadence_months ? `${doc.review_cadence_months} mo` : "—"],
                  ["Next review", doc.next_review_at?.slice(0, 10)]].map(([k, v]) => (
                  <div key={k as string} className="flex justify-between py-1 text-[12.5px]">
                    <span className="text-txt3">{k}</span><span className="font-medium capitalize">{(v as string) || "—"}</span>
                  </div>))}
              </Card>
            </div>
          </div>)}

      {tab === "versions" && (
        <Card>
          {doc.versions.map((v) => (
            <div key={v.id} className="flex items-center gap-3 border-t border-bd py-2.5 text-[13px] first:border-t-0">
              <span className={cn("h-2 w-2 rounded-full", v.id === doc.current_published_version_id ? "bg-ok" : "bg-bd")} />
              <span className="font-mono font-semibold">v{v.version_label}</span>
              <Pill tone={statusTone(v.status)}>{v.status.charAt(0) + v.status.slice(1).toLowerCase()}</Pill>
              <span className="text-txt3">{v.published_at?.slice(0, 10) ?? "—"}</span>
              <span className="truncate text-txt2">{v.changelog ?? ""}</span>
              <span className="ml-auto flex shrink-0 gap-1.5">
                {v.file_id && <a href={`/api/documents/${doc.id}/versions/${v.id}/render.pdf`} target="_blank" className="rounded-md border border-bd px-2 py-1 text-[12px] hover:bg-canvas">PDF</a>}
              </span>
            </div>
          ))}
          {doc.versions.length > 1 && (
            <button onClick={() => setDiffing(true)} className="btn mt-3">Compare versions (diff)</button>)}
        </Card>
      )}

      {tab === "approvals" && <ApprovalsTab doc={doc} />}

      {diffing && <DiffModal docId={doc.id} versions={doc.versions} onClose={() => setDiffing(false)} />}
    </>
  );
}
