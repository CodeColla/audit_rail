import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, downloadFile, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { FilePreview } from "../components/FilePreview";
import { ControlPicker } from "../components/ControlPicker";
import { Card, cn, Drawer, inputCls, Loading, Modal, Pill, Segment, Table, Td } from "../lib/ui";

type Detail = {
  id: string; title: string; bank_name: string; status: string; predicted_verdict: string;
  total_questions: number; answered: number; open_high_findings: number; score_pct: number;
};
type Row = {
  question_id: string; number: string; text: string; section: string;
  mapped_control: string | null; mapped_control_statement: string | null;
  response_value: string | null; workflow_status: string; evidence_count: number;
};
type RespDetail = {
  question: { id: string; number: string; text: string };
  mapped_control: { code: string; statement: string } | null;
  response: { id: string; response_value: string; comment: string | null; na_justification: string | null; workflow_status: string } | null;
  evidence: { id: string; title: string; evidence_type: string }[];
  inherited_evidence: { id: string; title: string; evidence_type: string; valid_until: string | null }[];
  revisions: { rev_no: number; response_value: string | null; comment: string | null; created_at: string }[];
  thread: { author_kind: string; kind: string; body: string; created_at: string }[];
  findings: { title: string; risk_rating: string | null; likelihood: number | null; impact: number | null }[];
};
type Ev = { id: string; title: string; evidence_type: string };

/** Click an evidence row to preview it inline, without leaving the audit response drawer —
 * mirrors Evidence.tsx's `<FilePreview url={`/evidence/${id}/file`} .../>` usage, but as a
 * Modal (which layers over the Drawer cleanly) rather than navigating away. */
function EvidencePreviewButton({ id, title }: { id: string; title: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} className="text-left text-[12.5px] font-medium text-ink hover:text-accent hover:underline">
        {title}
      </button>
      {open && (
        <Modal open onClose={() => setOpen(false)} title={title} size="lg">
          <FilePreview url={`/evidence/${id}/file`} name={title} />
        </Modal>
      )}
    </>
  );
}

const VALUES = [
  { v: "yes", label: "Yes", tone: "ok" }, { v: "partial", label: "Partial", tone: "warn" },
  { v: "no", label: "No", tone: "bad" }, { v: "na", label: "N/A", tone: "na" },
];
const STATUSES = ["draft", "in_progress", "submitted", "in_review",
                  "verdict_issued", "closed"] as const;
// these must be workflow_status values (schema: open|answered|ask_pending|actioned|
// validated|final). "na" used to be listed here, but that is a RESPONSE value — the chip
// could never match anything.
const FILTERS = ["all", "open", "answered", "ask_pending", "actioned",
                 "validated", "final"] as const;

function QuestionDrawer({ aid, qid, onClose }: { aid: string; qid: string; onClose: () => void }) {
  const can = useCan();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["resp", aid, qid], queryFn: () => get<RespDetail>(`/assessments/${aid}/responses/${qid}`) });

  const [val, setVal] = useState(""); const [comment, setComment] = useState("");
  const [just, setJust] = useState(""); const [msg, setMsg] = useState(""); const [kind, setKind] = useState("action");
  const [pickEv, setPickEv] = useState(false); const [showFinding, setShowFinding] = useState(false);
  const [fTitle, setFTitle] = useState(""); const [fL, setFL] = useState(2); const [fI, setFI] = useState(2);
  const [remapping, setRemapping] = useState(false);
  const [remapNotice, setRemapNotice] = useState("");

  useEffect(() => {
    if (data?.response) { setVal(data.response.response_value ?? ""); setComment(data.response.comment ?? ""); setJust(data.response.na_justification ?? ""); }
    setRemapping(false); setRemapNotice("");
  }, [data?.question.id]); // reset when a different question loads

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["resp", aid, qid] });
    qc.invalidateQueries({ queryKey: ["grid", aid] });
    qc.invalidateQueries({ queryKey: ["assessment", aid] });
  };
  const saveAnswer = useMutation({
    mutationFn: () => api.put(`/assessments/${aid}/responses/${qid}`, { response_value: val, comment, na_justification: just || null }),
    onSuccess: refresh,
  });
  const remap = useMutation({
    mutationFn: (control_id: string) => api.patch(`/assessments/${aid}/responses/${qid}/mapping`, { control_id }),
    onSuccess: (r) => {
      setRemapping(false);
      // Never silently rewrites an already-saved answer — same "report, don't rewrite"
      // rule P4-S5 uses for editing a control's stock_response.
      setRemapNotice(r.data.was_prefilled
        ? "Re-mapped. The saved answer was auto-filled from the old control and was NOT changed — review it."
        : "Re-mapped.");
      refresh();
    },
  });
  const postMsg = useMutation({
    mutationFn: () => api.post(`/assessments/${aid}/messages`, { kind, body: msg, question_id: qid }),
    onSuccess: () => { setMsg(""); refresh(); },
  });
  const linkEv = useMutation({
    mutationFn: (evId: string) => api.post(`/assessments/${aid}/responses/${qid}/evidence`, { evidence_id: evId }),
    onSuccess: () => { setPickEv(false); refresh(); },
  });
  const raiseFinding = useMutation({
    mutationFn: () => api.post(`/assessments/${aid}/findings`, { title: fTitle, response_id: data?.response?.id, likelihood: fL, impact: fI }),
    onSuccess: () => { setShowFinding(false); setFTitle(""); refresh(); },
  });
  const evList = useQuery({ queryKey: ["evidence", ""], queryFn: () => get<Ev[]>("/evidence"), enabled: pickEv });

  if (!data) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  const naNoJust = val === "na" && !just.trim();
  const canEdit = can("audits", "edit");

  return (
    <Drawer open onClose={onClose} sub={`QUESTION · #${data.question.number}`} title={data.question.text}>
      {/* mapped control — statement was fetched but never rendered; re-map lets a reviewer
          fix a wrong auto-assignment without leaving the audit response */}
      <Card>
        <div className="mb-1.5 flex items-center justify-between">
          <div className="eyebrow">Mapped control{data.mapped_control && (
            <span className="ml-2 font-mono normal-case tracking-normal text-accent">↳ {data.mapped_control.code}</span>)}</div>
          {canEdit && (
            <button onClick={() => setRemapping((s) => !s)} className="text-[12px] font-medium text-accent">Re-map</button>
          )}
        </div>
        {data.mapped_control
          ? <p className="text-[12.5px] text-txt2">{data.mapped_control.statement}</p>
          : <p className="text-[12.5px] text-txt3">No control mapped to this question yet.</p>}
        {remapNotice && (
          <div className="mt-2 rounded-md bg-warn-bg px-2.5 py-1.5 text-[11.5px] text-warn">{remapNotice}</div>
        )}
        {remapping && (
          <div className="mt-2">
            <ControlPicker onClose={() => setRemapping(false)}
              onPick={(c) => remap.mutate(c.id)} />
          </div>
        )}
      </Card>

      {/* answer / edit — read-only for anyone without audits.edit */}
      <Card>
        <div className="eyebrow mb-2.5">Your answer</div>
        {canEdit ? (
          <>
            <Segment value={val} onChange={setVal} options={VALUES} />
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Comment / how you meet this control…"
              className={cn(inputCls, "mt-3 min-h-[64px]")} />
            {val === "na" && (
              <textarea value={just} onChange={(e) => setJust(e.target.value)} placeholder="N/A justification (required)…"
                className={cn(inputCls, "mt-2 min-h-[48px] border-l-[3px] border-l-warn")} />
            )}
            <div className="mt-3 flex items-center gap-2">
              <button disabled={!val || naNoJust || saveAnswer.isPending} onClick={() => saveAnswer.mutate()}
                className="btn btn-primary disabled:opacity-50">{saveAnswer.isPending ? "Saving…" : "Save answer"}</button>
              {data.response && <Pill tone={data.response.workflow_status}>{data.response.workflow_status.replace(/_/g, " ")}</Pill>}
              {naNoJust && <span className="text-[11.5px] text-bad">N/A needs a justification</span>}
            </div>
          </>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Pill tone={val || "na"}>{(val || "unanswered").toUpperCase()}</Pill>
              {data.response && <Pill tone={data.response.workflow_status}>{data.response.workflow_status.replace(/_/g, " ")}</Pill>}
            </div>
            {comment && <p className="text-[13px] text-txt2">{comment}</p>}
            {just && <p className="border-l-[3px] border-l-warn pl-2 text-[12.5px] text-txt2">{just}</p>}
            <span className="text-[11.5px] text-txt3">Read-only — you don't have permission to answer audit points.</span>
          </div>
        )}
      </Card>

      {/* evidence — direct (attached to this response) vs inherited (attached to the
          mapped control, P4-S5's evidence_controls) are badged distinctly and kept as two
          separate lists, never merged, so nothing downstream double-counts. */}
      <Card>
        <div className="mb-2 flex items-center justify-between">
          <div className="eyebrow">Linked evidence</div>
          {canEdit && (
            <button onClick={() => setPickEv((s) => !s)} className="text-[12px] font-medium text-accent"
              disabled={!data.response}>＋ Link evidence</button>
          )}
        </div>
        {!data.response && <div className="text-[12px] text-txt3">Answer the question first, then link evidence.</div>}
        {data.evidence.map((e) => (
          <div key={e.id} className="flex items-center gap-2.5 border-t border-bd py-2 first:border-t-0">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-info-bg text-info">▣</span>
            <div>
              <EvidencePreviewButton id={e.id} title={e.title} />
              <div className="text-[11px] text-txt3">{e.evidence_type}</div>
            </div>
          </div>
        ))}
        {data.evidence.length === 0 && data.response && (
          <div className="border-t border-bd py-2 text-[12px] text-txt3 first:border-t-0">Nothing attached directly to this question.</div>
        )}
        {pickEv && (
          <div className="mt-2 max-h-40 overflow-y-auto rounded-md border border-bd">
            {(evList.data ?? []).map((e) => (
              <button key={e.id} onClick={() => linkEv.mutate(e.id)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-[12.5px] hover:bg-canvas">
                <span>{e.title}</span><span className="text-txt3">link →</span>
              </button>
            ))}
            {evList.data?.length === 0 && <div className="px-3 py-2 text-[12px] text-txt3">No evidence in the vault yet.</div>}
          </div>
        )}
      </Card>

      {data.inherited_evidence.length > 0 && (
        <Card>
          <div className="mb-2 flex items-center gap-2">
            <div className="eyebrow">Inherited from {data.mapped_control?.code}</div>
            <span className="rounded bg-canvas px-1.5 py-0.5 text-[10px] font-medium text-txt3">via control</span>
          </div>
          {data.inherited_evidence.map((e) => (
            <div key={e.id} className="flex items-center gap-2.5 border-t border-bd py-2 first:border-t-0">
              <span className="grid h-7 w-7 place-items-center rounded-md bg-canvas text-txt3">▣</span>
              <div>
                <EvidencePreviewButton id={e.id} title={e.title} />
                <div className="text-[11px] text-txt3">{e.evidence_type}{e.valid_until && ` · valid until ${e.valid_until.slice(0, 10)}`}</div>
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* answer history — written on every save; used to be invisible */}
      {(data.revisions?.length ?? 0) > 1 && (
        <Card>
          <div className="eyebrow mb-2">Answer history · {data.revisions.length} revisions</div>
          {data.revisions.map((r) => (
            <div key={r.rev_no} className="flex items-baseline gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
              <span className="font-mono text-[11px] text-txt3">v{r.rev_no}</span>
              <Pill tone={r.response_value ?? "na"}>{(r.response_value ?? "—").toUpperCase()}</Pill>
              <span className="min-w-0 flex-1 truncate text-txt2">{r.comment}</span>
              <span className="shrink-0 text-[11px] text-txt3">{(r.created_at ?? "").slice(0, 10)}</span>
            </div>
          ))}
        </Card>
      )}

      {/* review thread */}
      <Card>
        <div className="eyebrow mb-3">Review thread</div>
        {data.thread.length === 0 ? <div className="text-[12.5px] text-txt3">No messages yet.</div>
          : <div className="mb-3 flex flex-col gap-3">
            {data.thread.map((m, i) => (
              <div key={i} className="flex gap-2.5">
                <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold text-white",
                  m.author_kind === "auditor" ? "bg-info" : "bg-ink")}>{m.author_kind === "auditor" ? "AU" : "ME"}</span>
                <div className="flex-1">
                  <div className="mb-0.5 text-[9.5px] font-bold uppercase tracking-wider text-txt2">{m.kind} · {m.author_kind}</div>
                  <div className={cn("rounded-lg border border-bd px-3 py-2 text-[13px]", m.author_kind === "auditor" && "border-transparent bg-info-bg")}>{m.body}</div>
                </div>
              </div>
            ))}
          </div>}
        {canEdit && (
          <div className="flex items-center gap-2">
            <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-md border border-bd px-2 py-2 text-[12.5px]">
              {["action", "validation", "remark", "ask"].map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <input value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="Reply on the thread…" className={inputCls} />
            <button disabled={!msg.trim() || postMsg.isPending} onClick={() => postMsg.mutate()} className="btn btn-primary disabled:opacity-50">Send</button>
          </div>
        )}
      </Card>

      {/* findings */}
      {data.findings.map((f, i) => (
        <div key={i} className="rounded-lg border border-bd border-l-[3px] border-l-warn bg-paper p-3.5">
          <div className="mb-1 flex items-center gap-2 text-[13.5px] font-semibold"><Pill tone={f.risk_rating ?? "na"}>{f.risk_rating ?? "—"}</Pill>{f.title}</div>
          {f.likelihood && f.impact && <div className="font-mono text-[12px] text-txt2">L {f.likelihood} × I {f.impact} = {f.likelihood * f.impact}</div>}
        </div>
      ))}
      {!canEdit ? null : showFinding ? (
        <Card>
          <div className="eyebrow mb-2">Raise a finding</div>
          <input value={fTitle} onChange={(e) => setFTitle(e.target.value)} placeholder="Finding title…" className={inputCls} />
          <div className="mt-2 flex items-center gap-3">
            <label className="text-[12px]">Likelihood <select value={fL} onChange={(e) => setFL(+e.target.value)} className="ml-1 rounded border border-bd px-2 py-1">{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></label>
            <label className="text-[12px]">Impact <select value={fI} onChange={(e) => setFI(+e.target.value)} className="ml-1 rounded border border-bd px-2 py-1">{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></label>
            <span className="font-mono text-[12px] text-txt2">= {fL * fI} ({fL * fI >= 6 ? "High" : fL * fI >= 2 ? "Medium" : "Low"})</span>
          </div>
          <button disabled={!fTitle.trim() || raiseFinding.isPending} onClick={() => raiseFinding.mutate()} className="btn btn-primary mt-3 disabled:opacity-50">Raise finding</button>
        </Card>
      ) : (
        <button onClick={() => setShowFinding(true)} className="btn self-start">＋ Raise a finding</button>
      )}
    </Drawer>
  );
}

type Guest = { id: string; email: string; full_name: string; expires_at: string | null; revoked_at: string | null };

function InviteModal({ aid, onClose }: { aid: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState(""); const [name, setName] = useState("");
  const [firm, setFirm] = useState(""); const [exp, setExp] = useState("");
  const [link, setLink] = useState("");
  const guests = useQuery({ queryKey: ["guests", aid], queryFn: () => get<Guest[]>(`/assessments/${aid}/guests`) });
  const invite = useMutation({
    mutationFn: () => api.post(`/assessments/${aid}/guests`, { email, full_name: name, firm, expires_at: exp || null }),
    onSuccess: (r) => {
      setLink(`${location.origin}/auditor?token=${r.data.access_token}`);
      setEmail(""); setName(""); setFirm("");
      qc.invalidateQueries({ queryKey: ["guests", aid] });
    },
  });
  const revoke = useMutation({
    mutationFn: (gid: string) => api.delete(`/assessments/${aid}/guests/${gid}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guests", aid] }),
  });

  return (
    <Modal open onClose={onClose} title="Auditor access">
      <div className="grid grid-cols-2 gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Auditor name" className={inputCls} />
        <input value={firm} onChange={(e) => setFirm(e.target.value)} placeholder="Firm (PwC…)" className={inputCls} />
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" className={inputCls} />
        <input type="date" value={exp} onChange={(e) => setExp(e.target.value)} className={inputCls} />
      </div>
      <button disabled={!email || !name || invite.isPending} onClick={() => invite.mutate()} className="btn btn-primary mt-3 w-full justify-center disabled:opacity-50">
        {invite.isPending ? "Inviting…" : "Invite & generate link"}
      </button>
      {link && (
        <div className="mt-3 rounded-md border border-bd bg-canvas p-2.5">
          <div className="eyebrow mb-1">Share this link with the auditor</div>
          <div className="flex gap-2">
            <input readOnly value={link} className={inputCls + " font-mono text-[11px]"} onFocus={(e) => e.target.select()} />
            <button onClick={() => navigator.clipboard?.writeText(link)} className="btn shrink-0">Copy</button>
          </div>
        </div>
      )}
      <div className="mt-4">
        <div className="eyebrow mb-1.5">Invited auditors</div>
        {(guests.data ?? []).length === 0 && <div className="text-[12.5px] text-txt3">None yet.</div>}
        {(guests.data ?? []).map((g) => (
          <div key={g.id} className="flex items-center gap-2 border-t border-bd py-2 text-[12.5px] first:border-t-0">
            <div><span className="font-medium">{g.full_name}</span> <span className="text-txt3">{g.email}</span></div>
            <div className="ml-auto flex items-center gap-2">
              <Pill tone={g.revoked_at ? "na" : "info"}>{g.revoked_at ? "revoked" : "active"}</Pill>
              {!g.revoked_at && <button onClick={() => revoke.mutate(g.id)} className="text-[12px] text-bad hover:underline">revoke</button>}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

export default function Workspace() {
  const { id } = useParams();
  const qc = useQueryClient();
  const canEdit = useCan()("audits", "edit");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [openQ, setOpenQ] = useState<string | null>(null);
  const [invite, setInvite] = useState(false);

  const det = useQuery({ queryKey: ["assessment", id], queryFn: () => get<Detail>(`/assessments/${id}`) });
  const grid = useQuery({ queryKey: ["grid", id], queryFn: () => get<Row[]>(`/assessments/${id}/questions`) });
  const prefill = useMutation({
    mutationFn: () => api.post(`/assessments/${id}/prefill`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["grid", id] }); qc.invalidateQueries({ queryKey: ["assessment", id] }); },
  });
  const setStatus = useMutation({
    mutationFn: (status: string) => api.patch(`/assessments/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assessment", id] }),
  });
  if (det.isLoading || grid.isLoading || !det.data) return <Loading />;
  const d = det.data;

  const counts: Record<string, number> = { all: (grid.data ?? []).length };
  (grid.data ?? []).forEach((r) => { counts[r.workflow_status] = (counts[r.workflow_status] ?? 0) + 1; });
  const rows = (grid.data ?? []).filter((r) => filter === "all" || r.workflow_status === filter);

  return (
    <>
      <Link to="/audits" className="text-[13px] text-txt2 hover:text-ink">← All audits</Link>
      <div className="mb-1 mt-2 flex items-end gap-3">
        <h1 className="text-[22px] font-semibold tracking-[-0.01em]">{d.bank_name} — {d.title}</h1>
        <Pill tone={d.status}>{d.status.replace(/_/g, " ")}</Pill>
        {/* The audit lifecycle was unreachable: PATCH /assessments/{id} existed but nothing
            called it, so every audit stayed "draft" forever. */}
        {canEdit && (
          <select value={d.status} aria-label="Audit status"
            onChange={(e) => setStatus.mutate(e.target.value)}
            className="rounded-md border border-bd bg-paper px-2 py-1 text-[12.5px] text-txt2 outline-none focus:border-accent">
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </select>
        )}
      </div>
      <p className="mb-4 flex flex-wrap items-center gap-x-1 gap-y-2 text-[13.5px] text-txt2">
        {d.total_questions} controls · {d.answered} answered · score <b className="tnum">{d.score_pct}%</b> · verdict{" "}
        <b className={d.predicted_verdict === "Satisfactory" ? "text-ok" : "text-warn"}>{d.predicted_verdict}</b>
        {d.open_high_findings > 0 && <> · {d.open_high_findings} open High</>}
        {canEdit && (
          <button onClick={() => prefill.mutate()} disabled={prefill.isPending} className="btn ml-2 py-1.5 disabled:opacity-50">
            {prefill.isPending ? "Prefilling…" : "Prefill from library"}
          </button>
        )}
        {canEdit && <button onClick={() => setInvite(true)} className="btn py-1.5">Invite auditor</button>}
        <button onClick={() => downloadFile(`/assessments/${id}/export.xlsx`, `${det.data.bank_name ?? "assessment"}.xlsx`)}
          className="btn py-1.5">Export ↓</button>
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={cn("rounded-full border px-3 py-1.5 text-[12.5px] font-medium capitalize",
              filter === f ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd bg-paper text-txt2")}>
            {f.replace(/_/g, " ")} <span className="ml-1 text-txt3 tnum">{counts[f] ?? 0}</span>
          </button>
        ))}
      </div>

      <Table head={["#", "Control question", "Response", "Workflow", "Evidence"]}>
        {rows.map((r) => (
          <tr key={r.question_id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenQ(r.question_id)}>
            <Td className="font-mono text-txt3">#{r.number}</Td>
            <Td><div className="font-medium">{r.text}</div>
              {r.mapped_control && (
                <div className="font-mono text-[11.5px] text-txt3" title={r.mapped_control_statement ?? undefined}>
                  ↳ {r.mapped_control}
                </div>
              )}</Td>
            <Td>{r.response_value ? <Pill tone={r.response_value}>{r.response_value.toUpperCase()}</Pill> : <span className="text-txt3">—</span>}</Td>
            <Td><Pill tone={r.workflow_status}>{r.workflow_status.replace(/_/g, " ")}</Pill></Td>
            <Td>{r.evidence_count > 0 ? <span className="rounded bg-canvas px-2 py-0.5 text-[11px]">{r.evidence_count} linked</span> : <span className="text-txt3">none</span>}</Td>
          </tr>
        ))}
      </Table>

      {openQ && <QuestionDrawer key={openQ} aid={id!} qid={openQ} onClose={() => setOpenQ(null)} />}
      {invite && <InviteModal aid={id!} onClose={() => setInvite(false)} />}
    </>
  );
}
