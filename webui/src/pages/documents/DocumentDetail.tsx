import { useEffect, useMemo, useRef, useState } from "react";
import { Plus } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile, errText, get } from "../../lib/api";
import { useAuth, useCan } from "../../lib/auth";
import { useDebounced } from "../../lib/useDebounced";
import { OrgLogo } from "../../components/Avatar";
import { DocBody } from "../../components/DocBody";
import { RichTextEditor } from "../../components/RichTextEditor";
import { SheetEditor } from "../../components/SheetEditor";
import { useDocTypes, useClassifications } from "./Documents";
import { Bar, Card, cn, inputCls, Loading, Modal, Pill } from "../../lib/ui";

type Version = {
  id: string; version_label: string; major: number; minor: number; status: string;
  content: string; content_format: "MARKDOWN" | "HTML" | "SHEET"; changelog: string | null;
  published_at: string | null; file_id: string | null;
  /** Server-rendered HTML for the editor; present on open_version only. See the Editor. */
  editor_html?: string;
};
/**
 * What the PDF and Word exports will actually be letterheaded with (P6-S5b).
 *
 * The org logo has driven every export header since P6-S5, but the only place to set it is
 * Admin → Organisation — nowhere near a document, so the natural question standing on this
 * page ("where do I set the header?") had no visible answer. This is the answer: the mark and
 * the name that will be printed, and a way to reach the setting.
 *
 * Deliberately not a new setting of its own. There is one organisation identity and it is
 * already editable; adding a second place to configure the same thing would create two
 * sources of truth for what a controlled document says it belongs to.
 */
function Letterhead() {
  const { user } = useAuth();
  const can = useCan();
  const org = user?.organisations?.find((o) => o.tenant_id === user?.tenant_id)?.name;
  const body = (
    <>
      <OrgLogo name={org} size="xs" />
      <span className="text-caption text-txt3">Letterhead · {org ?? "your organisation"}</span>
    </>
  );
  const title = "Printed at the top of every page of the PDF and Word exports"
    + (can("org", "edit") ? " — click to change the logo" : "");
  return can("org", "edit") ? (
    <Link to="/admin" title={title} aria-label={title}
      className="ml-auto flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-canvas">
      {body}
    </Link>
  ) : (
    <span title={title} className="ml-auto flex items-center gap-1.5">{body}</span>
  );
}

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
type SaveState = "idle" | "saving" | "saved" | "error";

/**
 * The editable surface. **Autosaves** — there is no Save button, no Done button (P6).
 *
 * The old shape was an editor you entered from a read view and left via "Save draft". That is
 * the defect this redesign exists to remove: a user *with* edit rights landed on a read-only
 * page and had to click to begin. Saving is now a background consequence of typing and the
 * save state is the only affordance.
 *
 * Only DRAFTS reach here — and that is not merely a UI rule. `freeze_published_version()`
 * refuses writes to a published version at the database level, so autosave cannot corrupt a
 * signed record even if this component were mounted on one by mistake.
 */
function Editor({ docId, version, onSaveStateChange }:
  { docId: string; version: Version; onSaveStateChange: (s: SaveState, err?: string) => void }) {
  const qc = useQueryClient();
  const isSheet = version.content_format === "SHEET";
  // `editor_html` is the server's HTML rendering of this draft. For a pre-S4 MARKDOWN version
  // it is md_to_html(content) — the editor must never be handed markdown, because TipTap
  // parses any string it is given as HTML and would collapse the document into one paragraph
  // of literal source, which the next save would persist. SHEET versions get no `editor_html`
  // at all (documents.py returns None): SheetEditor wants the raw JSON in `content`.
  const initial = version.editor_html ?? version.content;
  const [content, setContent] = useState(initial);
  const [changelog, setChangelog] = useState(version.changelog ?? "");
  const dirty = content !== initial || changelog !== (version.changelog ?? "");

  const save = useMutation({
    mutationFn: (payload: { content: string; changelog: string }) =>
      api.patch(`/documents/${docId}/versions/${version.id}`,
                { ...payload, content_format: isSheet ? "SHEET" : "HTML" }),
    onMutate: () => onSaveStateChange("saving"),
    onSuccess: () => {
      // Refresh the version list and review dates — but never unmount the editor. The whole
      // point of the redesign is that the surface does not go away.
      qc.invalidateQueries({ queryKey: ["document", docId] });
      onSaveStateChange("saved");
    },
    // A failed save used to be indistinguishable from a successful one: the button reverted
    // to "Save draft" and the author walked away believing it had landed.
    onError: (e: any) => onSaveStateChange("error", errText(e, "could not save")),
  });

  // Debounced autosave: 1.2s idle is long enough not to write on every keystroke, short
  // enough that little is lost if the tab closes. The ref carries the latest values so the
  // timer is not re-armed by its own dependencies.
  const latest = useRef({ content, changelog });
  latest.current = { content, changelog };
  useEffect(() => {
    if (!dirty) return;
    const t = setTimeout(() => save.mutate(latest.current), 1200);
    return () => clearTimeout(t);
  }, [content, changelog]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Autosave debounces, so a tab closed inside that window still loses the last edit.
  useEffect(() => {
    if (!dirty && !save.isPending) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, save.isPending]);

  // Blur commits immediately instead of waiting out the debounce — clicking away is the
  // clearest "done with that thought" signal a text surface gets.
  return (
    <div onBlur={() => { if (dirty) save.mutate(latest.current); }}>
      {isSheet
        ? <SheetEditor value={content} onChange={setContent} />
        : <RichTextEditor value={content} onChange={setContent} docId={docId} />}
      <input value={changelog} onChange={(e) => setChangelog(e.target.value)}
        aria-label="Changelog" placeholder="Changelog — what changed in this version?"
        className={inputCls + " mt-4"} />
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
      <div className="mb-3 flex items-center gap-2 text-sm">
        <select value={from} onChange={(e) => setFrom(e.target.value)} className={inputCls}>
          {versions.map((v) => <option key={v.id} value={v.id}>v{v.version_label}</option>)}
        </select>
        <span className="text-txt3">→</span>
        <select value={to} onChange={(e) => setTo(e.target.value)} className={inputCls}>
          {versions.map((v) => <option key={v.id} value={v.id}>v{v.version_label}</option>)}
        </select>
      </div>
      {from === to ? <div className="text-sm text-txt3">Pick two different versions.</div>
        : diff.isLoading ? <div className="text-sm text-txt3">Loading…</div>
        : (
          <>
            <div className="mb-2 flex gap-3 text-label">
              <span className="text-ok">+{diff.data?.added ?? 0} added</span>
              <span className="text-bad">−{diff.data?.removed ?? 0} removed</span>
              <span className="text-txt3">v{vlabel(from)} → v{vlabel(to)}</span>
            </div>
            <pre className="max-h-[55vh] overflow-auto rounded-md border border-bd bg-canvas p-3 font-mono text-label leading-relaxed">
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
      <p className="mb-3 text-label text-txt2">
        Pick approvers, then set how many must approve — this is the <b>M of N</b> threshold, frozen
        now and unchangeable mid-flight. Departed staff aren't shown.
      </p>
      <div className="max-h-52 overflow-y-auto rounded-md border border-bd">
        {active.map((p) => (
          <label key={p.id} className="flex cursor-pointer items-center gap-2.5 border-b border-bd px-3 py-2 text-sm last:border-0 hover:bg-canvas">
            <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
            {p.full_name}
          </label>
        ))}
        {active.length === 0 && <div className="px-3 py-2 text-label text-txt3">No active people — add some in People first.</div>}
      </div>
      <div className="mt-3 flex items-center gap-2 text-sm">
        <span>Threshold</span>
        <button onClick={() => setThreshold((t) => Math.max(1, t - 1))} className="grid h-7 w-7 place-items-center rounded-md border border-bd">−</button>
        <span className="w-8 text-center font-semibold tnum">{threshold}</span>
        <button onClick={() => setThreshold((t) => Math.min(Math.max(1, picked.length), t + 1))} className="grid h-7 w-7 place-items-center rounded-md border border-bd">+</button>
        <span className="text-txt3">of {picked.length} picked</span>
      </div>
      {err && <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
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

  if (!open) return <Card><div className="text-sm text-txt3">
    No version in progress. Use <b>Edit</b> to start a new version, then request approval here.</div></Card>;

  if (open.status === "DRAFT")
    return (
      <>
        <Card>
          <div className="eyebrow mb-1">Draft v{open.version_label}</div>
          <p className="text-sm text-txt2">This draft hasn't been sent for approval yet.
            An approved quorum is required before it can be published — including minor versions.</p>
          {can("documents", "edit") && (
            <button onClick={() => setSubmitting(true)} className="btn btn-primary mt-3">Request approval</button>
          )}
        </Card>
        {submitting && <SubmitModal docId={doc.id} versionId={open.id} onClose={() => setSubmitting(false)} />}
      </>
    );

  // PENDING_APPROVAL with a round
  if (!appr) return <Card><div className="text-sm text-txt3">No approval round.</div></Card>;
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
          <div key={d.approver_person_id} className="flex items-center gap-3 border-t border-bd py-2 text-sm first:border-t-0">
            <span className={cn("grid h-6 w-6 place-items-center rounded-full text-caption text-white",
              d.state === "APPROVED" ? "bg-ok" : d.state === "REJECTED" ? "bg-bad" : "bg-txt3")}>
              {d.state === "APPROVED" ? "✓" : d.state === "REJECTED" ? "✕" : "○"}</span>
            <span className="font-medium">{d.full_name}</span>
            <span className="text-txt3">{d.state.toLowerCase()}{d.comment ? ` — "${d.comment}"` : ""}</span>
            {d.state === "PENDING" && can("documents", "approve") && (
              <span className="ml-auto flex gap-1.5">
                <button onClick={() => decide.mutate({ pid: d.approver_person_id, state: "APPROVED" })}
                  className="rounded-md border border-ok/40 px-2 py-1 text-label text-ok hover:bg-ok-bg">Approve</button>
                <button onClick={() => decide.mutate({ pid: d.approver_person_id, state: "REJECTED" })}
                  className="rounded-md border border-bd px-2 py-1 text-label text-txt2 hover:border-bad hover:text-bad">Reject</button>
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
        {!appr.can_publish && <span className="text-label text-txt3">{appr.approved} of {appr.threshold_required} approvals — publish unlocks at {appr.threshold_required}.</span>}
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
        <p className="text-sm text-txt2">Everyone in the audience has already signed the current
          version — nothing to send.</p>
      ) : (
        <>
          <p className="mb-3 text-label text-txt2">
            Each link is <b>single-use</b> and tied to one person. Email delivery comes later — for now,
            copy a link (or the CSV) and share it. Opening it <b>logged out</b> shows the policy and a
            signature box.
          </p>
          <button onClick={downloadCsv} className="btn mb-3">⬇ Download all as CSV</button>
          <div className="max-h-[46vh] divide-y divide-bd overflow-y-auto rounded-md border border-bd">
            {issued.map((i) => (
              <div key={i.person_id} className="flex items-center gap-2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{i.full_name}</div>
                  <div className="truncate font-mono text-caption text-txt3">{link(i.token)}</div>
                </div>
                <button onClick={() => copy(i.person_id, link(i.token))}
                  className="btn shrink-0 py-1 text-label">{copied === i.person_id ? "Copied ✓" : "Copy"}</button>
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
    return <Card><div className="text-sm text-txt2">
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
          <div className="text-sm text-txt2">
            <span className="text-title font-semibold text-ink tnum">{c ? (c.coverage_pct ?? 0) : 0}%</span>
            {c && <span className="ml-2">{c.signed} of {c.expected} signed</span>}
          </div>
        </div>
        <Bar pct={pct} />
        {c && c.expected === 0 && (
          <p className="mt-3 text-label text-txt3">No one is in the audience yet. Set one below,
            then start a campaign.</p>)}
        {c && c.people.length > 0 && (
          <div className="mt-3 max-h-64 divide-y divide-bd overflow-y-auto">
            {c.people.map((p) => (
              <div key={p.person_id} className="flex items-center gap-3 py-1.5 text-sm">
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
              className={cn("rounded-full border px-3 py-1.5 text-label font-medium",
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
              <label key={p.id} className="flex cursor-pointer items-center gap-2.5 border-b border-bd px-3 py-2 text-sm last:border-0 hover:bg-canvas">
                <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
                {p.full_name}{p.department && <span className="text-txt3">· {p.department}</span>}
              </label>))}
            {activePeople.length === 0 && <div className="px-3 py-2 text-label text-txt3">No active people.</div>}
          </div>)}

        {err && <div className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}

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
          <span className="text-label text-txt3">
            Currently targeting <b className="text-txt2">{aud.data?.targeted ?? 0}</b> active {(aud.data?.targeted ?? 0) === 1 ? "person" : "people"}.
          </span>
        </div>
      </Card>
      )}

      {issued !== null && <LinksModal issued={issued} onClose={() => setIssued(null)} />}
    </div>
  );
}

/** 816×1056 = 8.5×11in at 96dpi, with ~1in margins. The page has to *be* a page, or the
 *  paper metaphor just reads as a card that happens to be white. */
const PAPER = "mx-auto min-h-[1056px] w-full max-w-[816px] rounded-sm bg-paper px-[92px] " +
  "py-[84px] shadow-[0_1px_3px_rgba(14,26,43,.12),0_8px_24px_rgba(14,26,43,.06)]";

// ─────────────────────────────────────────────────────── save indicator
const SAVE_LABEL: Record<SaveState, string> = {
  idle: "All changes saved", saving: "Saving…", saved: "All changes saved",
  error: "Couldn't save — retrying on your next edit",
};

function SaveIndicator({ state, error }: { state: SaveState; error?: string }) {
  return (
    // `data-save-state` is machine-readable on purpose: "idle" (nothing to save yet) and
    // "saved" (a write just succeeded) deliberately share the same words, so asserting on the
    // TEXT would pass before anything had been saved at all.
    <span data-save-state={state}
      className={cn("flex shrink-0 items-center gap-1.5 whitespace-nowrap text-caption",
        state === "error" ? "text-bad" : "text-txt3")}>
      <span aria-hidden="true">{state === "saving" ? "◌" : state === "error" ? "⚠" : "✓"}</span>
      <span role="status">{state === "error" && error ? `Couldn't save — ${error}` : SAVE_LABEL[state]}</span>
    </span>
  );
}

// ─────────────────────────────────────────────────────── details edit form
/**
 * Issue #13: Type, Classification, Owner and Review were all set once at creation and never
 * editable again — the Details tab was a plain read-only `.map()` over label/value pairs.
 * The backend already accepted classification/owner/review_cadence_months; document_type and
 * next_review_at needed the PATCH schema extended too (see `DocumentPatch`). Modeled on
 * EvidenceDetail.tsx's `EditForm`: local state, one PATCH, invalidate + close.
 */
function DetailsEditForm({ doc, onDone }: { doc: Detail; onDone: () => void }) {
  const qc = useQueryClient();
  const types = useDocTypes();
  const classes = useClassifications();
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const [f, setF] = useState({
    document_type: doc.document_type, classification: doc.classification,
    owner_person_id: doc.owner?.id ?? "",
    review_cadence_months: doc.review_cadence_months ? String(doc.review_cadence_months) : "",
    next_review_at: doc.next_review_at?.slice(0, 10) ?? "",
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const save = useMutation({
    mutationFn: () => api.patch(`/documents/${doc.id}`, {
      ...f, review_cadence_months: f.review_cadence_months ? +f.review_cadence_months : null,
      next_review_at: f.next_review_at || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", doc.id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      onDone();
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });
  return (
    <div className="flex flex-col gap-3">
      <label className="text-label font-medium">Type
        <select value={f.document_type} onChange={(e) => set("document_type")(e.target.value)}
          className={inputCls + " mt-1"}>
          {(types.data ?? []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select></label>
      <label className="text-label font-medium">Classification
        <select value={f.classification} onChange={(e) => set("classification")(e.target.value)}
          className={inputCls + " mt-1 capitalize"}>
          {(classes.data ?? []).map((c) => (
            <option key={c.id} value={c.value}>{c.value.charAt(0) + c.value.slice(1).toLowerCase()}</option>
          ))}
        </select></label>
      <label className="text-label font-medium">Owner
        <select value={f.owner_person_id} onChange={(e) => set("owner_person_id")(e.target.value)}
          className={inputCls + " mt-1"}>
          <option value="">— pick a person —</option>
          {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
        </select></label>
      <label className="text-label font-medium">Review every (months)
        <input type="number" value={f.review_cadence_months}
          onChange={(e) => set("review_cadence_months")(e.target.value)} className={inputCls + " mt-1"} /></label>
      <label className="text-label font-medium">Next review
        <input type="date" value={f.next_review_at}
          onChange={(e) => set("next_review_at")(e.target.value)} className={inputCls + " mt-1"} /></label>
      {err && <div className="rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
      <div className="flex gap-2">
        <button disabled={!f.owner_person_id || save.isPending}
          onClick={() => save.mutate()} className="btn btn-primary disabled:opacity-50">
          {save.isPending ? "Saving…" : "Save"}</button>
        <button type="button" onClick={onDone} className="btn">Cancel</button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────── linked controls / audit points
type LinkedControl = { id: string; code: string; statement: string };
type LinkedAuditPoint = {
  assessment_id: string; assessment_title: string; bank_name: string;
  question_id: string; number: string; text: string;
};
type DocLinks = { controls: LinkedControl[]; audit_points: LinkedAuditPoint[] };
type LiteControl = { id: string; code: string; statement: string };

/**
 * Controls this document proves, from the document's side. `control_documents` is writable
 * from the control's own page too (library.py's link_control_document/unlink_control_document)
 * — both caches must be invalidated or one screen shows a link the other does not, same
 * "two doors" note as EvidenceDetail.tsx's LinkControls.
 */
function LinkedControlsCard({ docId, controls, canEdit }:
  { docId: string; controls: LinkedControl[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [term, setTerm] = useState("");
  const dq = useDebounced(term);
  const [err, setErr] = useState("");
  const list = useQuery({
    queryKey: ["controls-lite", dq], enabled: picking,
    queryFn: () => get<LiteControl[]>(`/library/controls?${new URLSearchParams(dq ? { q: dq } : {})}`),
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["document-links", docId] });
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["control"] });   // the control page's own link list
  };
  const link = useMutation({
    mutationFn: (control_id: string) => api.post(`/library/controls/${control_id}/documents`, { document_id: docId }),
    onSuccess: () => { setErr(""); setPicking(false); setTerm(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not link.")),
  });
  const unlink = useMutation({
    mutationFn: (control_id: string) => api.delete(`/library/controls/${control_id}/documents/${docId}`),
    onSuccess: () => { setErr(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not unlink.")),
  });
  const linkedIds = new Set(controls.map((c) => c.id));
  const pickable = (list.data ?? []).filter((c) => !linkedIds.has(c.id));

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <div className="eyebrow">Linked controls · {controls.length}</div>
        {canEdit && <button onClick={() => setPicking((s) => !s)}
          className="text-label font-medium text-accent"><Plus size={14} strokeWidth={2.4} /> Link</button>}
      </div>
      {controls.length === 0 && <p className="text-label text-txt3">Not linked to any control yet.</p>}
      {controls.map((c) => (
        <div key={c.id} className="flex items-center gap-2 border-t border-bd py-2 text-label first:border-t-0">
          <Link to={`/controls/view/${c.id}`} className="shrink-0 font-mono font-semibold text-accent hover:underline">{c.code}</Link>
          <span className="min-w-0 flex-1 truncate text-txt2">{c.statement}</span>
          {canEdit && <button onClick={() => unlink.mutate(c.id)} className="shrink-0 text-caption text-bad hover:underline">remove</button>}
        </div>
      ))}
      {err && <div className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
      {picking && (
        <div className="mt-2">
          <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)}
            placeholder="Search controls…" className={inputCls} />
          <div className="mt-2 max-h-48 overflow-y-auto rounded-md border border-bd">
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

/**
 * Audit points this document is attached to directly. Attach reuses the existing
 * `POST /assessments/{aid}/responses/{qid}/documents` (assessments.py's link_document) via a
 * two-step assessment → question picker — a `response_documents` row only exists once a
 * question has been answered, so "an audit" alone isn't the linkable unit, a response is.
 * Detach reuses the shared `DELETE .../documents/{document_id}` route the Workspace drawer
 * already uses (issue #13 Phase 1), so both surfaces stay in sync off the one endpoint.
 */
function LinkedAuditPointsCard({ docId, points, canEdit }:
  { docId: string; points: LinkedAuditPoint[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [assessmentId, setAssessmentId] = useState("");
  const [err, setErr] = useState("");
  const assessments = useQuery({
    queryKey: ["assessments-lite"], enabled: picking,
    queryFn: () => get<{ id: string; title: string; bank_name: string }[]>("/assessments"),
  });
  const questions = useQuery({
    queryKey: ["grid", assessmentId], enabled: picking && !!assessmentId,
    queryFn: () => get<{ question_id: string; number: string; text: string }[]>(
      `/assessments/${assessmentId}/questions`),
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["document-links", docId] });
    qc.invalidateQueries({ queryKey: ["resp"] });   // the Workspace drawer's own cache
  };
  const link = useMutation({
    mutationFn: (question_id: string) =>
      api.post(`/assessments/${assessmentId}/responses/${question_id}/documents`, { document_id: docId }),
    onSuccess: () => { setErr(""); setPicking(false); setAssessmentId(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not link — answer the question first.")),
  });
  const unlink = useMutation({
    mutationFn: (p: { assessment_id: string; question_id: string }) =>
      api.delete(`/assessments/${p.assessment_id}/responses/${p.question_id}/documents/${docId}`),
    onSuccess: () => { setErr(""); done(); },
    onError: (e: any) => setErr(errText(e, "Could not unlink.")),
  });
  const linkedQIds = new Set(points.filter((p) => p.assessment_id === assessmentId).map((p) => p.question_id));
  const pickableQuestions = (questions.data ?? []).filter((q) => !linkedQIds.has(q.question_id));

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <div className="eyebrow">Linked audit points · {points.length}</div>
        {canEdit && <button onClick={() => setPicking((s) => !s)}
          className="text-label font-medium text-accent"><Plus size={14} strokeWidth={2.4} /> Link</button>}
      </div>
      {points.length === 0 && <p className="text-label text-txt3">Not linked to any audit point yet.</p>}
      {points.map((p) => (
        <div key={p.question_id} className="flex items-center gap-2 border-t border-bd py-2 text-label first:border-t-0">
          <Link to={`/audits/${p.assessment_id}`} className="min-w-0 flex-1 truncate font-medium text-ink hover:text-accent">
            <span className="font-mono text-txt3">No. {p.number}</span> · {p.bank_name} — {p.assessment_title}
          </Link>
          {canEdit && <button
            onClick={() => unlink.mutate({ assessment_id: p.assessment_id, question_id: p.question_id })}
            className="shrink-0 text-caption text-bad hover:underline">remove</button>}
        </div>
      ))}
      {err && <div className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
      {picking && (
        <div className="mt-2 flex flex-col gap-2">
          <select value={assessmentId} onChange={(e) => setAssessmentId(e.target.value)} className={inputCls}>
            <option value="">— pick an audit —</option>
            {(assessments.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>{a.bank_name} — {a.title}</option>))}
          </select>
          {assessmentId && (
            <div className="max-h-48 overflow-y-auto rounded-md border border-bd">
              {questions.isPending ? (
                <div className="px-3 py-2 text-label text-txt3">Loading…</div>
              ) : pickableQuestions.length === 0 ? (
                <div className="px-3 py-2 text-label text-txt3">Nothing left to link in this audit.</div>
              ) : pickableQuestions.map((q) => (
                <button key={q.question_id} onClick={() => link.mutate(q.question_id)}
                  className="flex w-full items-start gap-2 px-3 py-2 text-left text-label hover:bg-canvas">
                  <span className="shrink-0 font-mono text-txt3">No. {q.number}</span>
                  <span className="min-w-0 flex-1 truncate text-txt2">{q.text}</span>
                </button>))}
            </div>
          )}
        </div>)}
    </Card>
  );
}

// ─────────────────────────────────────────────────────── compliance rail
/**
 * Governance lives in a collapsible rail, not in tabs above the content.
 *
 * The page used to be four tabs — content · versions · approvals · attestation — so reading
 * the document and checking who had approved it were mutually exclusive, and three quarters
 * of the tab strip was metadata competing with the thing itself. Every panel here is data
 * `GET /documents/{id}` already returns; nothing new is fetched.
 */
type RailTab = "details" | "approvals" | "versions" | "attest";

function ComplianceRail({ doc, onClose, onDiff }:
  { doc: Detail; onClose: () => void; onDiff: () => void }) {
  const qc = useQueryClient();
  const can = useCan();
  const [tab, setTab] = useState<RailTab>("details");
  const [editingDetails, setEditingDetails] = useState(false);
  const canEditDetails = can("documents", "edit");
  const links = useQuery({
    queryKey: ["document-links", doc.id], enabled: tab === "details",
    queryFn: () => get<DocLinks>(`/documents/${doc.id}/links`),
  });
  const [err, setErr] = useState("");
  const draft = doc.open_version?.status === "DRAFT" ? doc.open_version : null;
  const discard = useMutation({
    mutationFn: () => api.delete(`/documents/${doc.id}/versions/${draft!.id}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["document", doc.id] }); },
    onError: (e: any) => setErr(errText(e, "Could not discard this draft.")),
  });
  const TABS: [RailTab, string][] = [
    ["details", "Details"], ["approvals", "Approvals"],
    ["versions", `Versions`], ["attest", "Attest"],
  ];
  return (
    <aside aria-label="Compliance"
      className="w-[312px] shrink-0 overflow-y-auto border-l border-bd bg-paper">
      <div className="flex items-center justify-between border-b border-bd px-4 py-3">
        <span className="eyebrow">Compliance</span>
        <button onClick={onClose} aria-label="Close compliance panel"
          className="grid h-7 w-7 place-items-center rounded-md text-txt2 hover:bg-canvas">✕</button>
      </div>
      <div className="flex border-b border-bd">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn("flex-1 px-1 py-2.5 text-caption font-semibold",
              tab === k ? "bg-[rgba(249,115,22,0.08)] text-accent" : "text-txt2 hover:bg-canvas")}>
            {label}{k === "versions" && ` ${doc.versions.length}`}
          </button>
        ))}
      </div>

      <div className="p-4">
        {tab === "details" && (
          <div className="flex flex-col gap-4">
            {editingDetails ? (
              <DetailsEditForm doc={doc} onDone={() => setEditingDetails(false)} />
            ) : (
              <div>
                {[["Type", doc.document_type.toLowerCase()],
                  ["Classification", doc.classification.toLowerCase()],
                  ["Owner", doc.owner?.full_name],
                  ["Review every", doc.review_cadence_months ? `${doc.review_cadence_months} mo` : "—"],
                  ["Next review", doc.next_review_at?.slice(0, 10)]].map(([k, v]) => (
                  <div key={k as string} className="flex justify-between gap-3 py-1.5 text-label">
                    <span className="shrink-0 text-txt3">{k}</span>
                    <span className="truncate font-medium capitalize">{(v as string) || "—"}</span>
                  </div>))}
                {canEditDetails && (
                  <button onClick={() => setEditingDetails(true)} className="btn mt-2 py-1 text-label">Edit details</button>
                )}
              </div>
            )}
            <LinkedControlsCard docId={doc.id} controls={links.data?.controls ?? []} canEdit={canEditDetails} />
            <LinkedAuditPointsCard docId={doc.id} points={links.data?.audit_points ?? []} canEdit={canEditDetails} />
          </div>
        )}
        {tab === "approvals" && <ApprovalsTab doc={doc} />}
        {tab === "attest" && <AttestationTab doc={doc} />}
        {tab === "versions" && (
          <>
            {doc.versions.map((v) => (
              <div key={v.id} className="border-t border-bd py-2.5 first:border-t-0">
                <div className="flex items-center gap-2">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full",
                    v.id === doc.current_published_version_id ? "bg-ok"
                      : v.status === "DRAFT" ? "bg-accent" : "bg-bd")} />
                  <span className="font-mono text-label font-semibold">v{v.version_label}</span>
                  <Pill tone={statusTone(v.status)}>{v.status.charAt(0) + v.status.slice(1).toLowerCase()}</Pill>
                </div>
                <div className="mt-1 pl-4 text-caption text-txt3">
                  {v.published_at?.slice(0, 10) ?? "not published"}
                  {v.changelog ? ` · ${v.changelog}` : ""}
                </div>
              </div>
            ))}
            {doc.versions.length > 1 && (
              <button onClick={onDiff} className="btn mt-3 w-full justify-center">Compare versions</button>)}
            {/* Discard used to sit on the editor's button bar, which autosave removed. It is
                version management, so it belongs here — and only when there is another
                version to fall back to: the API 409s on discarding the only one, so offering
                it on a brand-new document was a guaranteed-to-fail affordance. */}
            {draft && doc.versions.length > 1 && can("documents", "edit") && (
              <button
                onClick={() => { if (confirm(
                    `Discard draft v${draft.version_label}?\n\nIts changes are lost. The last ` +
                    `published version is unaffected.`)) discard.mutate(); }}
                disabled={discard.isPending}
                className="btn mt-2 w-full justify-center text-bad hover:border-bad disabled:opacity-50">
                {discard.isPending ? "Discarding…" : `Discard draft v${draft.version_label}`}
              </button>)}
            {err && <div role="alert" className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-caption text-bad">{err}</div>}
          </>
        )}
      </div>
    </aside>
  );
}

// ─────────────────────────────────────────────────────── page
export default function DocumentDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const can = useCan();
  const [railOpen, setRailOpen] = useState(false);
  const [diffing, setDiffing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveErr, setSaveErr] = useState<string>();
  const [err, setErr] = useState("");

  const { data: doc, isLoading } = useQuery({
    queryKey: ["document", id], queryFn: () => get<Detail>(`/documents/${id}`) });

  const newDraft = useMutation({
    mutationFn: (bump: string) => api.post(`/documents/${id}/versions`, { bump }),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["document", id] }); },
    // Calling this on a PENDING_APPROVAL version 409s ("already an open draft") — that used
    // to vanish with no feedback at all, indistinguishable from nothing happening.
    onError: (e: any) => setErr(errText(e, "Could not start a new draft.")),
  });
  // Archive/Restore is a real capability, not chrome — retiring a policy without deleting it
  // is how a compliance record stops applying while staying auditable. It lived on the old
  // action row and has to survive the header rework.
  const archive = useMutation({
    mutationFn: (status: string) => api.patch(`/documents/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e: any) => setErr(errText(e, "Could not change the document status.")),
  });
  const rename = useMutation({
    mutationFn: (title: string) => api.patch(`/documents/${id}`, { title }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e: any) => setErr(errText(e, "Could not rename this document.")),
  });
  // issue #13: Archive was the only removal option — a document that was truly created by
  // mistake had no way to actually go away. Blocked server-side (409, naming what's
  // blocking it) when the document is a Statement of Applicability's cited artifact, an
  // access-review campaign's output, or the org's own NDA; Archive remains the right tool
  // for "stop applying but keep the record."
  const del = useMutation({
    mutationFn: () => api.delete(`/documents/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["documents"] }); nav("/documents"); },
    onError: (e: any) => setErr(errText(e, "Could not delete this document.")),
  });

  const shown = useMemo(() => {
    if (!doc) return null;
    const pub = doc.versions.find((v) => v.id === doc.current_published_version_id);
    return doc.open_version ?? pub ?? doc.versions[0] ?? null;
  }, [doc]);

  if (isLoading || !doc) return <Loading />;

  // THE state machine of this page, and the only read-only state that is legitimate.
  // Editable = an open DRAFT + the permission. Anything else is a published or in-approval
  // record, which is the audit trail and must not be editable at all.
  const draft = doc.open_version?.status === "DRAFT" ? doc.open_version : null;
  const canEdit = can("documents", "edit") && doc.status !== "ARCHIVED";
  const editable = !!draft && canEdit;
  const isSheet = (shown?.content_format ?? "HTML") === "SHEET";

  return (
    // 57px = SHELL_HEADER_H (lib/ui.tsx) — the Shell app header's rendered height. Tailwind
    // needs this as a literal here (JIT can't read the JS constant), so if the Shell header's
    // height ever changes, this and STICKY_BELOW_HEADER both need updating together.
    <div className="-mx-6 -mt-6 flex min-h-[calc(100vh-57px)] flex-col">
      {/* ── header: identity and actions ─────────────────────────────── */}
      <header className="shrink-0 border-b border-bd bg-paper px-5 pt-2.5">
        <div className="flex items-center gap-3.5">
          <Link to="/documents" className="shrink-0 text-label text-txt2 hover:text-ink">← All documents</Link>
          <span className="h-[22px] w-px shrink-0 bg-bd" />
          {/* Rename in place — no modal, no separate field. contentEditable rather than an
              <input> so the title keeps document-heading weight instead of looking like a
              form control sitting in the chrome. */}
          <h1
            contentEditable={canEdit}
            suppressContentEditableWarning
            spellCheck={false}
            onBlur={(e) => {
              const next = e.currentTarget.textContent?.trim() ?? "";
              if (next && next !== doc.title) rename.mutate(next);
              else e.currentTarget.textContent = doc.title;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
              if (e.key === "Escape") { e.currentTarget.textContent = doc.title; e.currentTarget.blur(); }
            }}
            className={cn("max-w-[460px] truncate rounded px-1.5 py-0.5 text-subtitle font-semibold outline-none",
              canEdit && "hover:bg-canvas focus:bg-canvas focus:shadow-[inset_0_0_0_1.5px_#F97316]")}>
            {doc.title}
          </h1>
          {shown && <Pill tone={statusTone(shown.status)}>
            {shown.status === "PENDING_APPROVAL" ? "In approval"
              : shown.status.charAt(0) + shown.status.slice(1).toLowerCase()} · v{shown.version_label}
          </Pill>}
          {doc.status === "ARCHIVED" && <Pill tone="na">Archived</Pill>}
          <span className="shrink-0 rounded border border-bd bg-canvas px-2 py-0.5 text-caption capitalize text-txt2">
            {doc.classification.toLowerCase()}
          </span>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <button onClick={() => setRailOpen((o) => !o)}
              className={cn("btn py-1.5", railOpen && "border-accent bg-[rgba(249,115,22,0.08)] text-accent")}>
              Compliance
            </button>
            <div className="relative">
              <button onClick={() => setExporting((o) => !o)} className="btn py-1.5">Export ▾</button>
              {exporting && shown && (
                <div className="absolute right-0 top-full z-30 mt-1 w-40 overflow-hidden rounded-md border border-bd bg-paper shadow-drawer">
                  {([["PDF", "render.pdf", "pdf"], ["DOCX", "render.docx", "docx"],
                     // Spreadsheets only — there is no sensible workbook projection of a prose
                     // policy and the API 400s if you ask for one. The .xlsx is a WORKING copy
                     // carrying live formulas; the PDF stays the controlled artefact.
                     ...(isSheet ? [["XLSX", "render.xlsx", "xlsx"]] : [])] as const).map(
                    ([label, route, ext]) => (
                      <button key={label} onClick={() => {
                        setExporting(false);
                        downloadFile(`/documents/${doc.id}/versions/${shown.id}/${route}`,
                                     `${doc.title} v${shown.version_label}.${ext}`);
                      }} className="block w-full px-3 py-2 text-left text-label hover:bg-canvas">{label}</button>
                    ))}
                </div>
              )}
            </div>
            {can("documents", "edit") && (
              <button onClick={() => archive.mutate(doc.status === "ARCHIVED" ? "ACTIVE" : "ARCHIVED")}
                disabled={archive.isPending} className="btn py-1.5 disabled:opacity-50">
                {doc.status === "ARCHIVED" ? "Restore" : "Archive"}
              </button>)}
            {can("documents", "delete") && (
              <button
                onClick={() => { if (confirm(
                    `Delete "${doc.title}"?\n\nEvery version, approval and attestation record ` +
                    `goes with it. This cannot be undone.`))
                    del.mutate(); }}
                disabled={del.isPending}
                className="btn py-1.5 text-bad hover:border-bad disabled:opacity-50">
                {del.isPending ? "Deleting…" : "Delete"}
              </button>)}
            {editable && (
              <button onClick={() => setSubmitting(true)} className="btn btn-primary py-1.5">
                Request sign-off
              </button>)}
          </div>
        </div>

        <div className="flex items-center gap-3 py-1.5">
          <span className="text-caption capitalize text-txt3">{doc.document_type.toLowerCase()}</span>
          <span className="text-caption text-txt3">Owner {doc.owner?.full_name ?? "—"}</span>
          {doc.next_review_at && (
            <span className={cn("text-caption", doc.review_status === "overdue" ? "text-bad" : "text-txt3")}>
              review {doc.next_review_at.slice(0, 10)}
            </span>)}
          {editable && <SaveIndicator state={saveState} error={saveErr} />}
          <Letterhead />
        </div>
      </header>

      {err && <div role="alert" className="mx-5 mt-3 rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}

      {/* ── the surface, and the rail ─────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">
        <div className={cn("min-w-0 flex-1", isSheet ? "bg-paper" : "bg-[#E9EBEE] px-6 py-6")}>
          {!editable && (
            /* The ONLY legitimate read-only state. Deliberately not phrased as "view mode":
               an approved record is the audit trail, and the way forward is a new draft. */
            <div className="mx-auto mb-5 max-w-[816px] rounded-lg border border-bd bg-paper px-4 py-3">
              <div className="text-label font-semibold">
                {doc.status === "ARCHIVED" ? "This document is archived."
                  : shown?.status === "PENDING_APPROVAL"
                    ? `v${shown.version_label} is out for approval and can't be edited.`
                    : `v${shown?.version_label ?? "1.0"} is approved and locked. Approved records can't be edited — that's the audit trail.`}
              </div>
              {canEdit && doc.status !== "ARCHIVED" && shown?.status !== "PENDING_APPROVAL" && (
                <button onClick={() => newDraft.mutate("minor")} disabled={newDraft.isPending}
                  className="btn btn-primary mt-2.5 py-1.5 disabled:opacity-50">
                  {newDraft.isPending ? "Starting…" : `Start v${nextMinor(shown?.version_label)} draft`}
                </button>)}
              {!canEdit && (
                <div className="mt-1 text-caption text-txt3">
                  You can read this document but not change it.
                </div>)}
            </div>
          )}

          {editable
            ? <div className={cn(!isSheet && PAPER)}>
                <Editor key={draft.id} docId={doc.id} version={draft}
                  onSaveStateChange={(s, e) => { setSaveState(s); setSaveErr(e); }} />
              </div>
            : <div className={cn(!isSheet && PAPER)}>
                <Body v={shown} />
              </div>}
        </div>

        {railOpen && <ComplianceRail doc={doc} onClose={() => setRailOpen(false)}
          onDiff={() => setDiffing(true)} />}
      </div>

      {diffing && <DiffModal docId={doc.id} versions={doc.versions} onClose={() => setDiffing(false)} />}
      {submitting && draft && (
        <SubmitModal docId={doc.id} versionId={draft.id} onClose={() => setSubmitting(false)} />)}
    </div>
  );
}

/** "1.0" -> "1.1". The server decides the real number; this is only for the button label. */
function nextMinor(label: string | undefined): string {
  const [maj, min] = (label ?? "1.0").split(".");
  return `${maj}.${Number(min ?? 0) + 1}`;
}
