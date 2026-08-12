import { useState } from "react";
import { Plus } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, get } from "../lib/api";
import { AttachmentLink } from "./AttachmentLink";
import { useAuth } from "../lib/auth";
import { Card, cn, Drawer, inputCls, Loading, Pill, Table, Td } from "../lib/ui";
import { Wordmark } from "./Brand";

type Detail = { id: string; title: string; bank_name: string; status: string; total_questions: number; answered: number };
type Row = {
  question_id: string; number: string; text: string; section: string;
  mapped_control: string | null; response_value: string | null; workflow_status: string; evidence_count: number;
};
type RespDetail = {
  question: { id: string; number: string; text: string };
  response: { id: string; response_value: string; comment: string | null; na_justification: string | null; workflow_status: string } | null;
  evidence: { id: string; title: string; evidence_type: string }[];
  thread: { author_kind: string; kind: string; body: string }[];
};

function AuditorDrawer({ aid, qid, onClose }: { aid: string; qid: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["resp", aid, qid], queryFn: () => get<RespDetail>(`/assessments/${aid}/responses/${qid}`) });
  const [remark, setRemark] = useState("");
  const [showF, setShowF] = useState(false);
  const [fTitle, setFTitle] = useState(""); const [fL, setFL] = useState(2); const [fI, setFI] = useState(2);

  const refresh = () => { qc.invalidateQueries({ queryKey: ["resp", aid, qid] }); qc.invalidateQueries({ queryKey: ["aud-grid", aid] }); };
  const ask = useMutation({
    mutationFn: () => api.post(`/assessments/${aid}/messages`, { kind: "ask", body: remark, question_id: qid }),
    onSuccess: () => { setRemark(""); refresh(); },
  });
  const finding = useMutation({
    mutationFn: () => api.post(`/assessments/${aid}/findings`, { title: fTitle, response_id: data?.response?.id, likelihood: fL, impact: fI }),
    onSuccess: () => { setShowF(false); setFTitle(""); refresh(); },
  });

  if (!data) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  const r = data.response;
  return (
    <Drawer open onClose={onClose} sub={`QUESTION · #${data.question.number}`} title={data.question.text}>
      <Card>
        <div className="eyebrow mb-2.5">Vendor answer <span className="ml-1 normal-case tracking-normal text-txt3">(read-only)</span></div>
        <div className="flex flex-wrap items-center gap-2">
          {r?.response_value ? <Pill tone={r.response_value}>{r.response_value.toUpperCase()}</Pill> : <Pill tone="open">not answered</Pill>}
          <Pill tone={r?.workflow_status ?? "open"}>{(r?.workflow_status ?? "open").replace(/_/g, " ")}</Pill>
        </div>
        {r?.comment && <p className="mt-3 text-sm">{r.comment}</p>}
        {r?.na_justification && (
          <div className="mt-3 rounded-md border-l-[3px] border-l-warn bg-warn-bg/40 p-2.5 text-label">
            <div className="eyebrow mb-1">N/A justification</div>{r.na_justification}
          </div>
        )}
      </Card>

      <Card>
        <div className="eyebrow mb-2">Evidence</div>
        {data.evidence.length === 0 ? <div className="text-label text-txt3">No evidence attached.</div>
          : data.evidence.map((e) => (
            <div key={e.id} className="border-t border-bd py-2 first:border-t-0">
              {/* P5-S3: same AttachmentLink the member-side workspace uses, so an auditor
                  gets the underline, the type glyph and inline preview instead of a bare
                  download. `fileUrl` is REQUIRED here: the default `/evidence/{id}/file` is
                  member-only and 403s for every guest — P4-S8's bug. This is the
                  assessment-scoped route, which is the only one a guest token can reach.
                  Read-only by construction: no upload affordance is rendered for guests. */}
              <AttachmentLink id={e.id} title={e.title}
                fileUrl={`/assessments/${aid}/evidence/${e.id}/file`} />
              <div className="pl-8 text-caption text-txt3">{e.evidence_type}</div>
            </div>
          ))}
        {/* downloadFile rejects on a failed request and nothing used to catch it: the axios
            interceptor only acts on 401, so a 404 or 410 made the click do nothing at all. */}
      </Card>

      <Card>
        <div className="eyebrow mb-3">Review thread</div>
        {data.thread.length === 0 ? <div className="mb-3 text-label text-txt3">No messages yet.</div>
          : <div className="mb-3 flex flex-col gap-3">
            {data.thread.map((m, i) => (
              <div key={i} className="flex gap-2.5">
                <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full text-micro font-bold text-white", m.author_kind === "auditor" ? "bg-info" : "bg-ink")}>{m.author_kind === "auditor" ? "AU" : "ME"}</span>
                <div className="flex-1">
                  <div className="mb-0.5 text-micro font-bold uppercase tracking-wider text-txt2">{m.kind} · {m.author_kind}</div>
                  <div className={cn("rounded-lg border border-bd px-3 py-2 text-sm", m.author_kind === "auditor" && "border-transparent bg-info-bg")}>{m.body}</div>
                </div>
              </div>
            ))}
          </div>}
        <div className="flex items-center gap-2">
          <input value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="Add a remark for the vendor…" className={inputCls} />
          <button disabled={!remark.trim() || ask.isPending} onClick={() => ask.mutate()} className="btn btn-primary disabled:opacity-50">Send</button>
        </div>
      </Card>

      {showF ? (
        <Card>
          <div className="eyebrow mb-2">Raise a finding</div>
          <input value={fTitle} onChange={(e) => setFTitle(e.target.value)} placeholder="Finding title…" className={inputCls} />
          <div className="mt-2 flex items-center gap-3">
            <label className="text-label">Likelihood <select value={fL} onChange={(e) => setFL(+e.target.value)} className="ml-1 rounded border border-bd px-2 py-1">{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></label>
            <label className="text-label">Impact <select value={fI} onChange={(e) => setFI(+e.target.value)} className="ml-1 rounded border border-bd px-2 py-1">{[1, 2, 3].map((n) => <option key={n}>{n}</option>)}</select></label>
            <span className="font-mono text-label text-txt2">= {fL * fI} ({fL * fI >= 6 ? "High" : fL * fI >= 2 ? "Medium" : "Low"})</span>
          </div>
          <button disabled={!fTitle.trim() || finding.isPending} onClick={() => finding.mutate()} className="btn btn-primary mt-3 disabled:opacity-50">Raise finding</button>
        </Card>
      ) : <button onClick={() => setShowF(true)} className="btn self-start"><Plus size={15} strokeWidth={2.4} /> Raise a finding</button>}
    </Drawer>
  );
}

export function AuditorApp() {
  const { user, logout } = useAuth();
  const aid = user!.assessment_id!;
  const [needsOnly, setNeedsOnly] = useState(false);
  const [openQ, setOpenQ] = useState<string | null>(null);

  const det = useQuery({ queryKey: ["assessment", aid], queryFn: () => get<Detail>(`/assessments/${aid}`) });
  const grid = useQuery({ queryKey: ["aud-grid", aid], queryFn: () => get<Row[]>(`/assessments/${aid}/questions`) });
  const findings = useQuery({ queryKey: ["aud-findings", aid], queryFn: () => get<any[]>(`/assessments/${aid}/findings`) });

  const rows = (grid.data ?? []).filter((r) => !needsOnly || r.workflow_status === "ask_pending");
  const needs = (grid.data ?? []).filter((r) => r.workflow_status === "ask_pending").length;

  return (
    <div className="min-h-screen bg-canvas">
      <div className="flex items-center gap-4 bg-ink px-8 py-4 text-white">
        <Wordmark size="md" tone="dark" />
        <span className="rounded-full bg-accent px-2.5 py-[3px] text-caption font-semibold text-ink">AUDITOR ACCESS</span>
        <div className="ml-auto text-right">
          <div className="text-sm font-medium">{user!.full_name}</div>
          <div className="text-caption text-[#6B7686]">reviewing for {det.data?.bank_name ?? "…"}</div>
        </div>
        <button onClick={logout} className="ml-4 rounded-md border border-[#26374d] px-3 py-1.5 text-sm hover:bg-[#17293f]">← Exit</button>
      </div>

      <div className="mx-auto max-w-[1080px] px-8 pb-16 pt-6">
        {det.isLoading || grid.isLoading || !det.data ? <Loading /> : (
          <>
            <div className="mb-5 flex items-center gap-2.5 rounded-xl border border-info/30 bg-info-bg px-4 py-3 text-label text-info">
              ⓘ You are reviewing <b className="mx-1">{det.data.title}</b> — read the vendor's responses & evidence, add remarks, and raise findings. Vendor answers are read-only.
            </div>
            <h1 className="text-title font-semibold tracking-[-0.01em]">{det.data.bank_name} — {det.data.title}</h1>
            <p className="mb-5 text-sm text-txt2">{det.data.total_questions} controls · {det.data.answered} answered · {findings.data?.length ?? 0} findings raised</p>

            <div className="mb-4 flex gap-2">
              <button onClick={() => setNeedsOnly(false)} className={cn("rounded-full border px-3 py-1.5 text-label font-medium", !needsOnly ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd bg-paper text-txt2")}>All <span className="ml-1 text-txt3 tnum">{grid.data?.length ?? 0}</span></button>
              <button onClick={() => setNeedsOnly(true)} className={cn("rounded-full border px-3 py-1.5 text-label font-medium", needsOnly ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink" : "border-bd bg-paper text-txt2")}>Needs my review <span className="ml-1 text-txt3 tnum">{needs}</span></button>
            </div>

            <Table head={["#", "Control question", "Response", "Evidence", ""]}>
              {rows.map((r) => (
                <tr key={r.question_id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenQ(r.question_id)}>
                  <Td className="font-mono text-txt3">#{r.number}</Td>
                  <Td><div className="font-medium">{r.text}</div>{r.mapped_control && <div className="font-mono text-caption text-txt3">↳ {r.mapped_control}</div>}</Td>
                  <Td>{r.response_value ? <Pill tone={r.response_value}>{r.response_value.toUpperCase()}</Pill> : <span className="text-txt3">—</span>}</Td>
                  <Td>{r.evidence_count > 0 ? <span className="rounded bg-canvas px-2 py-0.5 text-caption">{r.evidence_count} linked</span> : <span className="text-txt3">none</span>}</Td>
                  <Td>{r.workflow_status === "ask_pending" && <Pill tone="ask_pending">review</Pill>}</Td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </div>

      {openQ && <AuditorDrawer key={openQ} aid={aid} qid={openQ} onClose={() => setOpenQ(null)} />}
    </div>
  );
}
