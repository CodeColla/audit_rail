import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { DocBody } from "../components/DocBody";
import { RichTextEditor } from "../components/RichTextEditor";
import { SheetEditor } from "../components/SheetEditor";
import { Bar, Card, cn, inputCls, Loading, Modal, Pill } from "../lib/ui";

type Version = {
  id: string; version_label: string; major: number; minor: number; status: string;
  content: string; content_format: "MARKDOWN" | "HTML" | "SHEET"; changelog: string | null;
  published_at: string | null; file_id: string | null;
  /** Server-rendered HTML for the editor; present on open_version only. See the Editor. */
  editor_html?: string;
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
  status: string;
  versions: Version[]; open_version: Version | null; approval: Approval | null;
};
type Person = { id: string; full_name: string; department: string | null; effective_state: string };
type AudienceRuleRow = { id: string; rule: string; value: string | null; person_id: string | null };
type CoveragePerson = {
  person_id: string; full_name: string; department: string | null; email: string;
  state: string; signed_at: string | null; due_at: string | null;
};
type Coverage = {
  published: boolean; version_label: string | null; expected: number; signed: number;
  outstanding: number; coverage_pct: number | null; people: CoveragePerson[];
};
type IssuedLink = { person_id: string; full_name: string; email: string; token: string; sign_path: string };

/** A version's body, in whichever format it was authored. See components/DocBody. */
const Body = ({ v }: { v: Version | null }) =>
  <DocBody content={v?.content ?? ""} format={v?.content_format} />;

const statusTone = (s: string) =>
  s === "PUBLISHED" ? "ok" : s === "PENDING_APPROVAL" ? "warn" : s === "SUPERSEDED" ? "na" : "info";

// ─────────────────────────────────────────────────────── editor
function Editor({ docId, version, onDone, onDirtyChange, canDiscard }:
  { docId: string; version: Version; onDone: () => void;
    onDirtyChange?: (dirty: boolean) => void; canDiscard: boolean }) {
  const qc = useQueryClient();
  const isSheet = version.content_format === "SHEET";
  // `editor_html` is the server's HTML rendering of this draft. For a pre-S4 MARKDOWN
  // version it is md_to_html(content) — the editor must never be handed markdown, because
  // TipTap parses any string it is given as HTML and would collapse the whole document
  // into one paragraph of literal source, which the next save would then persist. SHEET
  // versions get no `editor_html` at all (documents.py returns None for them) — SheetEditor
  // wants the raw JSON in `content`, not an HTML rendering of it.
  const initial = version.editor_html ?? version.content;
  const [content, setContent] = useState(initial);
  const [changelog, setChangelog] = useState(version.changelog ?? "");
  const [discarding, setDiscarding] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const dirty = content !== initial || changelog !== (version.changelog ?? "");
  // Reports dirty state up so the PARENT's own exit routes (tab clicks, "All documents")
  // can guard too — "Done" isn't the only way out of this component. The parent unmounts
  // Editor directly on a tab click, which used to bypass this component's own guard
  // entirely because that guard only covered the Done button.
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty]);

  const save = useMutation({
    // A version's format never changes mid-edit (SHEET stays SHEET) — only a pre-S4
    // MARKDOWN version gets migrated to HTML on first edit through the rich text editor,
    // per the editor_html note above.
    mutationFn: () => api.patch(`/documents/${docId}/versions/${version.id}`,
                                { content, changelog, content_format: isSheet ? "SHEET" : "HTML" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", docId] }); onDone(); },
  });
  const discard = useMutation({
    mutationFn: () => api.delete(`/documents/${docId}/versions/${version.id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["document", docId] }); onDone(); },
  });

  // The body lives in local state until saved, so a reload or a closed tab loses it.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <input value={changelog} onChange={(e) => setChangelog(e.target.value)}
          placeholder="Changelog — what changed in this version?" className={inputCls} />
        <button disabled={save.isPending} onClick={() => save.mutate()}
          className="btn btn-primary shrink-0 disabled:opacity-50">{save.isPending ? "Saving…" : "Save draft"}</button>
        <button onClick={() => (dirty ? setLeaving(true) : onDone())}
          className="btn shrink-0">Done</button>
        {/* A document must keep at least one version — the API 409s discarding the only
            one. Offering the button anyway on every brand-new document (the most common
            time to be in the editor) was a guaranteed-to-fail affordance. */}
        {canDiscard && (
          <button onClick={() => setDiscarding(true)}
            className="btn shrink-0 text-bad hover:border-bad">Discard draft</button>
        )}
      </div>
      {/* A failed save used to be indistinguishable from a successful one: the button
          simply reverted to "Save draft" and the author walked away thinking it landed. */}
      {save.isError && (
        <div role="alert" className="mb-2 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">
          Could not save this draft — {errText(save.error)}. Your text is still here; try again.
        </div>
      )}
      {isSheet
        ? <SheetEditor value={content} onChange={setContent} />
        : <RichTextEditor value={content} onChange={setContent} />}

      <Modal open={leaving} onClose={() => setLeaving(false)} title="Leave without saving?">
        <p className="text-[13px] text-txt2">
          You have unsaved changes in this draft. Leaving the editor discards them.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={() => setLeaving(false)} className="btn">Keep editing</button>
          <button onClick={() => { setLeaving(false); save.mutate(); }}
            className="btn btn-primary">Save and close</button>
          <button onClick={() => { setLeaving(false); onDone(); }}
            className="btn text-bad hover:border-bad">Discard changes</button>
        </div>
      </Modal>

      <Modal open={discarding} onClose={() => setDiscarding(false)} title="Discard this draft?">
        <p className="text-[13px] text-txt2">
          Draft v{version.version_label} will be deleted and the document reverts to its last
          published version. This cannot be undone.
        </p>
        {discard.isError && (
          <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">
            {errText(discard.error)}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={() => setDiscarding(false)} className="btn">Keep editing</button>
          <button disabled={discard.isPending} onClick={() => discard.mutate()}
            className="btn btn-primary disabled:opacity-50">
            {discard.isPending ? "Discarding…" : "Discard draft"}</button>
        </div>
      </Modal>
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
    onError: (e: any) => setErr(errText(e, "Could not submit.")),
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
  const can = useCan();
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
          {can("documents", "edit") && (
            <button onClick={() => setSubmitting(true)} className="btn btn-primary mt-3">Request approval</button>
          )}
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
            {d.state === "PENDING" && can("documents", "approve") && (
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
        {can("documents", "publish") && (
          <button disabled={!appr.can_publish || publish.isPending} onClick={() => publish.mutate()}
            className="btn btn-primary disabled:opacity-50">
            {publish.isPending ? "Publishing…" : `Publish v${open.version_label}`}
          </button>
        )}
        {!appr.can_publish && <span className="text-[12px] text-txt3">{appr.approved} of {appr.threshold_required} approvals — publish unlocks at {appr.threshold_required}.</span>}
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────── issued-links modal
function LinksModal({ issued, onClose }: { issued: IssuedLink[]; onClose: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const link = (tok: string) => `${window.location.origin}/sign/${tok}`;
  const copy = (id: string, s: string) => { navigator.clipboard?.writeText(s); setCopied(id); };
  const downloadCsv = () => {
    const esc = (c: string) => `"${c.replace(/"/g, '""')}"`;
    const rows = [["name", "email", "signing_link"],
      ...issued.map((i) => [i.full_name, i.email, link(i.token)])];
    const blob = new Blob([rows.map((r) => r.map(esc).join(",")).join("\n")],
      { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "attestation-links.csv"; a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <Modal open onClose={onClose} title={`${issued.length} signing link${issued.length === 1 ? "" : "s"}`}>
      {issued.length === 0 ? (
        <p className="text-[13px] text-txt2">Everyone in the audience has already signed the current
          version — nothing to send.</p>
      ) : (
        <>
          <p className="mb-3 text-[12.5px] text-txt2">
            Each link is <b>single-use</b> and tied to one person. Email delivery comes later — for now,
            copy a link (or the CSV) and share it. Opening it <b>logged out</b> shows the policy and a
            signature box.
          </p>
          <button onClick={downloadCsv} className="btn mb-3">⬇ Download all as CSV</button>
          <div className="max-h-[46vh] divide-y divide-bd overflow-y-auto rounded-md border border-bd">
            {issued.map((i) => (
              <div key={i.person_id} className="flex items-center gap-2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{i.full_name}</div>
                  <div className="truncate font-mono text-[11px] text-txt3">{link(i.token)}</div>
                </div>
                <button onClick={() => copy(i.person_id, link(i.token))}
                  className="btn shrink-0 py-1 text-[12px]">{copied === i.person_id ? "Copied ✓" : "Copy"}</button>
              </div>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}

// ─────────────────────────────────────────────────────── attestation tab
function AttestationTab({ doc }: { doc: Detail }) {
  const qc = useQueryClient();
  const can = useCan();
  const published = !!doc.current_published_version_id;
  const cov = useQuery({
    queryKey: ["coverage", doc.id], enabled: published,
    queryFn: () => get<Coverage>(`/documents/${doc.id}/coverage`),
  });
  const aud = useQuery({
    queryKey: ["audiences", doc.id],
    queryFn: () => get<{ rules: AudienceRuleRow[]; targeted: number }>(`/documents/${doc.id}/audiences`),
  });
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const depts = useQuery({ queryKey: ["departments"],
    queryFn: () => get<{ department: string; count: number }[]>("/people/departments") });

  const [mode, setMode] = useState<"ALL_EMPLOYEES" | "DEPARTMENT" | "EXPLICIT">("ALL_EMPLOYEES");
  const [dept, setDept] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [issued, setIssued] = useState<IssuedLink[] | null>(null);
  const [err, setErr] = useState("");
  const [hydrated, setHydrated] = useState(false);

  // seed the builder from whatever audience is already saved (once)
  useEffect(() => {
    if (hydrated || !aud.data) return;
    const rules = aud.data.rules;
    if (rules.some((r) => r.rule === "ALL_EMPLOYEES")) setMode("ALL_EMPLOYEES");
    else if (rules.some((r) => r.rule === "DEPARTMENT")) {
      setMode("DEPARTMENT"); setDept(rules.find((r) => r.rule === "DEPARTMENT")?.value ?? "");
    } else if (rules.some((r) => r.rule === "EXPLICIT")) {
      setMode("EXPLICIT");
      setPicked(rules.filter((r) => r.rule === "EXPLICIT").map((r) => r.person_id!).filter(Boolean));
    }
    setHydrated(true);
  }, [aud.data, hydrated]);

  const rulesPayload = () =>
    mode === "ALL_EMPLOYEES" ? [{ rule: "ALL_EMPLOYEES" }]
      : mode === "DEPARTMENT" ? [{ rule: "DEPARTMENT", value: dept }]
      : picked.map((id) => ({ rule: "EXPLICIT", person_id: id }));

  const save = useMutation({
    mutationFn: () => api.post(`/documents/${doc.id}/audiences`, { rules: rulesPayload() }),
    onSuccess: () => { setErr("");
      qc.invalidateQueries({ queryKey: ["audiences", doc.id] });
      qc.invalidateQueries({ queryKey: ["coverage", doc.id] }); },
    onError: (e: any) => setErr(errText(e, "Could not save the audience.")),
  });
  const campaign = useMutation({
    mutationFn: () => api.post(`/documents/${doc.id}/attestation-campaign`, {}),
    onSuccess: (r) => { setErr(""); setIssued(r.data.issued);
      qc.invalidateQueries({ queryKey: ["coverage", doc.id] }); },
    onError: (e: any) => setErr(errText(e, "Could not start the campaign.")),
  });

  const activePeople = (people.data ?? []).filter((p) => p.effective_state === "ACTIVE");
  const toggle = (id: string) =>
    setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id]);
  const saveDisabled = save.isPending ||
    (mode === "DEPARTMENT" && !dept) || (mode === "EXPLICIT" && picked.length === 0);

  if (!published)
    return <Card><div className="text-[13px] text-txt2">
      Attestation is for a <b>published</b> document — publish a version first, then require the
      right people to read and sign it.</div></Card>;

  const c = cov.data;
  const pct = c?.coverage_pct ?? 0;

  return (
    <div className="space-y-4">
      {/* coverage */}
      <Card>
        <div className="mb-2 flex items-end justify-between">
          <div className="eyebrow">Coverage · v{c?.version_label ?? doc.versions.find((v) => v.id === doc.current_published_version_id)?.version_label}</div>
          <div className="text-[13px] text-txt2">
            <span className="text-[20px] font-semibold text-ink tnum">{c ? (c.coverage_pct ?? 0) : 0}%</span>
            {c && <span className="ml-2">{c.signed} of {c.expected} signed</span>}
          </div>
        </div>
        <Bar pct={pct} />
        {c && c.expected === 0 && (
          <p className="mt-3 text-[12.5px] text-txt3">No one is in the audience yet. Set one below,
            then start a campaign.</p>)}
        {c && c.people.length > 0 && (
          <div className="mt-3 max-h-64 divide-y divide-bd overflow-y-auto">
            {c.people.map((p) => (
              <div key={p.person_id} className="flex items-center gap-3 py-1.5 text-[13px]">
                <span className="font-medium">{p.full_name}</span>
                {p.department && <span className="text-txt3">{p.department}</span>}
                <span className="ml-auto">
                  {p.state === "SIGNED"
                    ? <Pill tone="ok">signed {p.signed_at?.slice(0, 10)}</Pill>
                    : <Pill tone="na">outstanding</Pill>}
                </span>
              </div>
            ))}
          </div>)}
      </Card>

      {/* audience builder — editing the audience is a documents.edit action */}
      {can("documents", "edit") && (
      <Card>
        <div className="eyebrow mb-2">Who must sign?</div>
        <div className="mb-3 flex flex-wrap gap-2">
          {([["ALL_EMPLOYEES", "All employees"], ["DEPARTMENT", "By department"],
             ["EXPLICIT", "Specific people"]] as const).map(([m, label]) => (
            <button key={m} onClick={() => setMode(m)}
              className={cn("rounded-full border px-3 py-1.5 text-[12.5px] font-medium",
                mode === m ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                  : "border-bd bg-paper text-txt2 hover:bg-canvas")}>{label}</button>
          ))}
        </div>

        {mode === "DEPARTMENT" && (
          <select value={dept} onChange={(e) => setDept(e.target.value)} className={inputCls + " mb-3"}>
            <option value="">— pick a department —</option>
            {(depts.data ?? []).map((d) => (
              <option key={d.department} value={d.department}>{d.department} ({d.count})</option>))}
          </select>)}

        {mode === "EXPLICIT" && (
          <div className="mb-3 max-h-52 overflow-y-auto rounded-md border border-bd">
            {activePeople.map((p) => (
              <label key={p.id} className="flex cursor-pointer items-center gap-2.5 border-b border-bd px-3 py-2 text-[13px] last:border-0 hover:bg-canvas">
                <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
                {p.full_name}{p.department && <span className="text-txt3">· {p.department}</span>}
              </label>))}
            {activePeople.length === 0 && <div className="px-3 py-2 text-[12.5px] text-txt3">No active people.</div>}
          </div>)}

        {err && <div className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}

        <div className="flex flex-wrap items-center gap-3">
          <button disabled={saveDisabled} onClick={() => save.mutate()}
            className="btn disabled:opacity-50">{save.isPending ? "Saving…" : "Save audience"}</button>
          {/* start_attestation_campaign is guarded server-side by documents.edit, not
              .publish — the UI must match, or an Editor sees the audience builder but
              never the button to actually use it. */}
          {can("documents", "edit") && (
            <button disabled={campaign.isPending || (aud.data?.targeted ?? 0) === 0}
              onClick={() => campaign.mutate()} className="btn btn-primary disabled:opacity-50">
              {campaign.isPending ? "Starting…" : "Start / resend campaign"}</button>
          )}
          <span className="text-[12.5px] text-txt3">
            Currently targeting <b className="text-txt2">{aud.data?.targeted ?? 0}</b> active {(aud.data?.targeted ?? 0) === 1 ? "person" : "people"}.
          </span>
        </div>
      </Card>
      )}

      {issued !== null && <LinksModal issued={issued} onClose={() => setIssued(null)} />}
    </div>
  );
}

// ─────────────────────────────────────────────────────── page
export default function DocumentDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const can = useCan();
  const [tab, setTab] = useState<"content" | "versions" | "approvals" | "attestation">("content");
  const [editing, setEditing] = useState(false);
  const [diffing, setDiffing] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);

  // A tab click or the "All documents" link unmounts Editor directly — its own unsaved-
  // changes modal never runs for those exits. Guard here too.
  const guardLeaveEditor = (proceed: () => void) => {
    if (editing && editorDirty
        && !window.confirm("You have unsaved changes in this draft. Leave and discard them?")) {
      return;
    }
    proceed();
  };

  const { data: doc, isLoading } = useQuery({ queryKey: ["document", id], queryFn: () => get<Detail>(`/documents/${id}`) });

  const [editErr, setEditErr] = useState("");
  const newDraft = useMutation({
    mutationFn: (bump: string) => api.post(`/documents/${id}/versions`, { bump }),
    onSuccess: () => {
      setEditErr("");
      qc.invalidateQueries({ queryKey: ["document", id] });
      setTab("content"); setEditing(true);
    },
    // Calling this on a PENDING_APPROVAL version 409s ("already an open draft") — that
    // used to vanish with no feedback at all, indistinguishable from nothing happening.
    onError: (e: any) => setEditErr(errText(e, "Could not start a new draft.")),
  });
  const archive = useMutation({
    mutationFn: (status: string) => api.patch(`/documents/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const shown = useMemo(() => {
    if (!doc) return null;
    const pub = doc.versions.find((v) => v.id === doc.current_published_version_id);
    return doc.open_version ?? pub ?? doc.versions[0] ?? null;
  }, [doc]);

  if (isLoading || !doc) return <Loading />;

  function onEdit() {
    // The editor only renders on the Content tab — clicking Edit from Versions,
    // Approvals or Attestation used to flip `editing` with no visible effect at all.
    if (doc!.open_version?.status === "DRAFT") { setTab("content"); setEditing(true); }
    else newDraft.mutate("minor");   // published → start a new minor draft
  }

  return (
    <>
      {editing && editorDirty ? (
        <button onClick={() => guardLeaveEditor(() => nav("/documents"))}
          className="text-[13px] text-txt2 hover:text-ink">← All documents</button>
      ) : (
        <Link to="/documents" className="text-[13px] text-txt2 hover:text-ink">← All documents</Link>
      )}
      <div className="mb-1 mt-2 flex items-end gap-3">
        <h1 className="text-[22px] font-semibold tracking-[-0.01em]">{doc.title}</h1>
        {shown && <Pill tone={statusTone(shown.status)}>{shown.status === "PENDING_APPROVAL" ? "In approval" : shown.status.charAt(0) + shown.status.slice(1).toLowerCase()}</Pill>}
        {doc.status === "ARCHIVED" && <Pill tone="na">Archived</Pill>}
        <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-[11px] capitalize text-txt2">{doc.classification.toLowerCase()}</span>
      </div>
      <p className="mb-3 flex flex-wrap items-center gap-x-1.5 text-[13.5px] text-txt2">
        <span className="capitalize">{doc.document_type.toLowerCase()}</span> · Owner {doc.owner?.full_name ?? "—"}
        {shown && <> · v{shown.version_label}</>}
        {doc.next_review_at && <> · <span className={doc.review_status === "overdue" ? "text-bad" : "text-txt2"}>review {doc.next_review_at.slice(0, 10)}</span></>}
        {can("documents", "edit") && (
          <button onClick={onEdit} disabled={newDraft.isPending} className="btn ml-2 py-1.5 disabled:opacity-50">
            {doc.open_version?.status === "DRAFT" ? "Continue editing" : "Edit → new version"}
          </button>
        )}
        {shown && (
          <button onClick={() => downloadFile(
            `/documents/${doc.id}/versions/${shown.id}/render.pdf`,
            `${doc.title} v${shown.version_label}.pdf`)}
            title={editing && editorDirty ? "Downloads the last SAVED draft — you have unsaved changes" : undefined}
            className="btn py-1.5">Export PDF ↓</button>)}
        {shown && (
          <button onClick={() => downloadFile(
            `/documents/${doc.id}/versions/${shown.id}/render.docx`,
            `${doc.title} v${shown.version_label}.docx`)}
            title={editing && editorDirty ? "Downloads the last SAVED draft — you have unsaved changes" : undefined}
            className="btn py-1.5">Export DOCX ↓</button>)}
        {can("documents", "edit") && (
          <button onClick={() => archive.mutate(doc.status === "ARCHIVED" ? "ACTIVE" : "ARCHIVED")}
            disabled={archive.isPending} className="btn py-1.5 disabled:opacity-50">
            {doc.status === "ARCHIVED" ? "Restore" : "Archive"}
          </button>)}
      </p>

      {editErr && (
        <div role="alert" className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{editErr}</div>
      )}
      {editing && editorDirty && (
        <div className="mb-3 rounded-md bg-warn-bg px-3 py-2 text-[12px] text-warn">
          Export downloads the last SAVED draft — you have unsaved changes in the editor.
        </div>
      )}

      {doc.open_version && doc.open_version.status === "DRAFT" && !editing && tab === "content" && (
        <div className="mb-4 rounded-lg border border-warn/40 bg-warn-bg/40 px-4 py-2.5 text-[12.5px] text-warn">
          Draft v{doc.open_version.version_label} in progress
          {can("documents", "edit") ? <>{" "}—{" "}
            <button onClick={() => setEditing(true)} className="font-semibold underline">continue editing</button>{" "}
            or go to <button onClick={() => setTab("approvals")} className="font-semibold underline">Approvals</button> to request sign-off.
          </> : <> — not yet published.</>}
        </div>
      )}

      <div className="mb-5 flex gap-1 border-b border-bd">
        {(["content", "versions", "approvals", "attestation"] as const).map((tt) => (
          <button key={tt} onClick={() => guardLeaveEditor(() => { setTab(tt); setEditing(false); })}
            className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-[13px] font-medium capitalize",
              tab === tt ? "border-accent text-ink" : "border-transparent text-txt2")}>
            {tt}{tt === "versions" && ` (${doc.versions.length})`}
          </button>
        ))}
      </div>

      {tab === "content" && (editing && doc.open_version
        ? <Editor docId={doc.id} version={doc.open_version} onDone={() => setEditing(false)}
                 onDirtyChange={setEditorDirty} canDiscard={doc.versions.length > 1} />
        : <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_240px]">
            <Card className="min-h-[40vh]"><Body v={shown} /></Card>
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
                {v.file_id && <button onClick={() => downloadFile(`/documents/${doc.id}/versions/${v.id}/render.pdf`, `${doc.title} v${v.version_label}.pdf`)} className="rounded-md border border-bd px-2 py-1 text-[12px] hover:bg-canvas">PDF</button>}
              </span>
            </div>
          ))}
          {doc.versions.length > 1 && (
            <button onClick={() => setDiffing(true)} className="btn mt-3">Compare versions (diff)</button>)}
        </Card>
      )}

      {tab === "approvals" && <ApprovalsTab doc={doc} />}
      {tab === "attestation" && <AttestationTab doc={doc} />}

      {diffing && <DiffModal docId={doc.id} versions={doc.versions} onClose={() => setDiffing(false)} />}
    </>
  );
}
