import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { Bar, Card, Loading, PageHead, Pill } from "../lib/ui";

type Dash = {
  kpis: { overall_readiness_pct: number; answered_from_stock_pct: number; open_auditor_asks: number; evidence_freshness_pct: number };
  readiness_by_bank: { assessment_id: string; bank_name: string; status: string; answered: number; total: number; pct: number }[];
  queues: {
    overdue_tasks: { run_id: string; title: string; due_at: string }[];
    expiring_evidence: { id: string; title: string; valid_until: string; status: string }[];
    policies_due: { id: string; title: string; next_review_at: string; review_status: string }[];
    open_auditor_asks: { id: string; number: string; text: string; bank_name: string }[];
  };
};

function Ring({ pct }: { pct: number }) {
  const r = 52, c = 2 * Math.PI * r, off = c * (1 - pct / 100);
  return (
    <svg width="120" height="120" viewBox="0 0 120 120" className="-rotate-90">
      <circle cx="60" cy="60" r={r} fill="none" stroke="#E4E7EB" strokeWidth="10" />
      <circle cx="60" cy="60" r={r} fill="none" stroke="#F97316" strokeWidth="10"
        strokeDasharray={c} strokeDashoffset={off} strokeLinecap="round" />
      <text x="60" y="58" transform="rotate(90 60 60)" textAnchor="middle"
        className="fill-ink text-[22px] font-semibold tnum">{pct}%</text>
      <text x="60" y="74" transform="rotate(90 60 60)" textAnchor="middle"
        className="fill-txt3 text-[9px] uppercase tracking-widest">ready</text>
    </svg>
  );
}

function Kpi({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <Card>
      <div className="eyebrow mb-2">{label}</div>
      <div className={"text-[29px] font-semibold leading-none tnum " + (accent ? "text-accent" : "")}>{value}</div>
      {sub && <div className="mt-2 text-[12px] text-txt2">{sub}</div>}
    </Card>
  );
}

function Queue({ title, count, tone, items }:
  { title: string; count: number; tone: string; items: { key: string; main: string; meta?: string; right?: React.ReactNode }[] }) {
  return (
    <Card>
      <h3 className="mb-1 flex items-center gap-2 text-[13.5px] font-semibold">
        {title}
        <span className={"rounded-full px-2 py-[1px] text-[11px] font-semibold " +
          (tone === "bad" ? "bg-bad text-white" : tone === "warn" ? "bg-warn text-white" : "bg-info text-white")}>{count}</span>
      </h3>
      {items.length === 0 && <div className="py-3 text-[12.5px] text-txt3">Nothing right now.</div>}
      {items.slice(0, 5).map((it) => (
        <div key={it.key} className="flex items-center gap-3 border-t border-bd py-2.5 first:border-t-0">
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium">{it.main}</div>
            {it.meta && <div className="text-[11.5px] text-txt3">{it.meta}</div>}
          </div>
          <div className="ml-auto shrink-0">{it.right}</div>
        </div>
      ))}
    </Card>
  );
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["dashboard"], queryFn: () => get<Dash>("/dashboard") });
  if (isLoading || !data) return <Loading />;
  const k = data.kpis, q = data.queues;

  return (
    <>
      <PageHead eyebrow="Audit readiness" title="Dashboard"
        lead="Where KIAM stands across every active bank assessment, and what needs attention next." />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi label="Overall readiness" value={`${k.overall_readiness_pct}%`} accent sub="across mapped questions" />
        <Kpi label="Answered from stock" value={`${k.answered_from_stock_pct}%`} sub="reused, not re-typed" />
        <Kpi label="Open auditor asks" value={String(k.open_auditor_asks)} sub="awaiting response" />
        <Kpi label="Evidence freshness" value={`${k.evidence_freshness_pct}%`} sub="within validity" />
      </div>

      <div className="mb-3 mt-7 flex items-center gap-2">
        <h2 className="text-[15px] font-semibold">Readiness by bank assessment</h2>
      </div>
      <Card className="grid gap-5 md:grid-cols-[150px_1fr] md:items-center">
        <div className="grid place-items-center"><Ring pct={k.overall_readiness_pct} /></div>
        <div className="flex flex-col justify-center gap-3.5">
          {data.readiness_by_bank.length === 0 && <div className="text-[13px] text-txt3">No assessments yet.</div>}
          {data.readiness_by_bank.map((b) => (
            <div key={b.assessment_id} className="grid grid-cols-[160px_1fr_44px] items-center gap-3">
              <div className="text-[13px] font-medium">{b.bank_name}
                <span className="block text-[11px] font-normal capitalize text-txt3">{b.status.replace(/_/g, " ")}</span>
              </div>
              <Bar pct={b.pct} muted={b.status === "submitted"} />
              <div className="text-right text-[13px] font-semibold tnum">{b.pct}%</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="mb-3 mt-7"><h2 className="text-[15px] font-semibold">Needs attention</h2></div>
      <div className="grid gap-4 md:grid-cols-2">
        <Queue title="Overdue recurring tasks" count={q.overdue_tasks.length} tone="bad"
          items={q.overdue_tasks.map((t) => ({ key: t.run_id, main: t.title, meta: `due ${t.due_at}`, right: <Pill tone="overdue">overdue</Pill> }))} />
        <Queue title="Evidence expiring" count={q.expiring_evidence.length} tone="warn"
          items={q.expiring_evidence.map((e) => ({ key: e.id, main: e.title, meta: `valid until ${e.valid_until}`, right: <Pill tone={e.status}>{e.status}</Pill> }))} />
        <Queue title="Policies due for review" count={q.policies_due.length} tone="warn"
          items={q.policies_due.map((p) => ({ key: p.id, main: p.title, meta: `review ${p.next_review_at}`, right: <Pill tone={p.review_status}>{p.review_status.replace("_", " ")}</Pill> }))} />
        <Queue title="Open auditor asks" count={q.open_auditor_asks.length} tone="info"
          items={q.open_auditor_asks.map((a) => ({ key: a.id, main: a.text.slice(0, 60), meta: `${a.bank_name} · #${a.number}`, right: <Pill tone="ask_pending">ask</Pill> }))} />
      </div>
    </>
  );
}
