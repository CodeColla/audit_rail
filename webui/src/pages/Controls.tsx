import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { Card, cn, Drawer, Loading, PageHead, Pill, Table, Td } from "../lib/ui";

type Domain = { id: string; code: string; name: string; control_count: number };
type Control = {
  id: string; code: string; statement: string; lifecycle: string; recurrence_months: number | null;
  applicability: string; reactivation_trigger: string | null; stock_response: string | null;
  domain_code: string; domain_name: string; mapped_count: number;
};
type MappedPoint = { bank_name: string; number: string; text: string; confidence: number; status: string };
type Xwalk = {
  columns: { id: string; bank_name: string; version_label: string }[];
  rows: { control_id: string; code: string; statement: string; domain_code: string; cells: Record<string, string[]> }[];
};

const lifecycleLabel = (c: Control) =>
  c.lifecycle === "recurring" ? `Recurring · ${c.recurrence_months}mo`
  : c.lifecycle === "one_time" ? "One-time" : "Per audit";

function ControlDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["control", id], queryFn: () => get<Control & { mapped_points: MappedPoint[] }>(`/library/controls/${id}`) });
  if (!data) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`STANDARD CONTROL · ${data.code}`} title={data.statement}>
      <div className="flex flex-wrap gap-2">
        <Pill tone="na">{lifecycleLabel(data)}</Pill>
        <Pill tone={data.applicability === "applicable" ? "applicable" : "na"}>
          {data.applicability === "applicable" ? "Applicable" : "Not applicable · dormant"}
        </Pill>
      </div>
      {data.applicability !== "applicable" && (
        <Card className="border-l-[3px] border-l-na">
          <div className="eyebrow mb-1">Dormant · reactivation trigger</div>
          <Pill tone="warn">{data.reactivation_trigger}</Pill>
          <p className="mt-2 text-[11.5px] text-txt3">Kept in the framework; reactivates automatically when this becomes true.</p>
        </Card>
      )}
      <Card>
        <div className="eyebrow mb-2.5">Mapped bank points — answer once, reuse everywhere</div>
        {data.mapped_points.length === 0 && <div className="text-[12.5px] text-txt3">No bank points mapped yet.</div>}
        {data.mapped_points.map((m, i) => (
          <div key={i} className="flex items-start gap-2.5 border-t border-bd py-2.5 first:border-t-0">
            <span className="min-w-[70px] shrink-0 rounded bg-ink px-2 py-0.5 text-center text-[11px] font-bold text-white">{m.bank_name.slice(0, 10)}</span>
            <div>
              <div className="text-[12.5px] leading-snug">{m.text}</div>
              <div className="font-mono text-[11px] text-txt3">point {m.number} · {(m.confidence * 100).toFixed(0)}% · {m.status}</div>
            </div>
          </div>
        ))}
      </Card>
    </Drawer>
  );
}

export default function Controls() {
  const [tab, setTab] = useState<"framework" | "crosswalk">("framework");
  const [domain, setDomain] = useState<string>("");
  const [openId, setOpenId] = useState<string | null>(null);

  const domains = useQuery({ queryKey: ["domains"], queryFn: () => get<Domain[]>("/library/domains") });
  const controls = useQuery({ queryKey: ["controls", domain], queryFn: () => get<Control[]>(`/library/controls${domain ? `?domain_code=${domain}` : ""}`) });
  const xwalk = useQuery({ queryKey: ["crosswalk"], queryFn: () => get<Xwalk>("/library/crosswalk"), enabled: tab === "crosswalk" });
  const total = useMemo(() => (domains.data ?? []).reduce((s, d) => s + d.control_count, 0), [domains.data]);

  return (
    <>
      <PageHead eyebrow="Standard control framework" title="Controls"
        lead="Your own master list of controls. Every bank's checklist points map onto these, so one answer serves every audit." />

      <div className="mb-5 flex gap-1 border-b border-bd">
        {(["framework", "crosswalk"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-[13px] font-medium capitalize",
              tab === t ? "border-accent text-ink" : "border-transparent text-txt2")}>
            {t === "crosswalk" ? "Bank crosswalk" : "Framework"}
          </button>
        ))}
      </div>

      {tab === "framework" ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[250px_1fr] md:items-start">
          <div className="card p-2">
            <button onClick={() => setDomain("")}
              className={cn("flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-[12.5px] font-medium",
                domain === "" ? "bg-[rgba(249,115,22,0.09)] font-semibold text-ink" : "text-txt2 hover:bg-canvas")}>
              All domains <span className="ml-auto text-[11px] text-txt3 tnum">{total}</span>
            </button>
            {(domains.data ?? []).map((d) => (
              <button key={d.id} onClick={() => setDomain(d.code)}
                className={cn("flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-[12.5px] font-medium",
                  domain === d.code ? "bg-[rgba(249,115,22,0.09)] font-semibold text-ink" : "text-txt2 hover:bg-canvas")}>
                <span className="font-mono text-[11px] text-accent">{d.code}</span>
                <span className="truncate">{d.name}</span>
                <span className="ml-auto text-[11px] text-txt3 tnum">{d.control_count}</span>
              </button>
            ))}
          </div>

          {controls.isLoading ? <Loading /> : (
            <Table head={["Ref", "Control", "Lifecycle", "Applicability", "Mapped"]}>
              {(controls.data ?? []).map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(c.id)}>
                  <Td className="font-mono font-semibold">{c.code}</Td>
                  <Td className="font-medium">{c.statement}
                    {c.applicability !== "applicable" && <span className="ml-2 rounded bg-na-bg px-1.5 py-0.5 text-[10px] text-na">dormant</span>}
                  </Td>
                  <Td><Pill tone={c.lifecycle === "per_audit" ? "na" : "warn"}>{lifecycleLabel(c)}</Pill></Td>
                  <Td><Pill tone={c.applicability === "applicable" ? "applicable" : "na"}>
                    {c.applicability === "applicable" ? "Applicable" : "Dormant"}</Pill></Td>
                  <Td><span className="rounded bg-canvas px-2 py-0.5 text-[11px] text-txt2">{c.mapped_count} pts</span></Td>
                </tr>
              ))}
            </Table>
          )}
        </div>
      ) : (
        <>
          <p className="mb-3 max-w-[70ch] text-[13.5px] text-txt2">
            Read one row across: answer the standard control once and it satisfies every bank point in that row.
          </p>
          {xwalk.isLoading || !xwalk.data ? <Loading /> : (
            <div className="overflow-x-auto rounded-xl border border-bd bg-paper">
              <table className="w-full text-[13px]">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-10 min-w-[240px] border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-txt3">Standard control</th>
                    {xwalk.data.columns.map((c) => (
                      <th key={c.id} className="border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-txt3">
                        {c.bank_name}<span className="block font-normal normal-case text-txt3">{c.version_label}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {xwalk.data.rows.map((r) => (
                    <tr key={r.control_id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(r.control_id)}>
                      <Td className="sticky left-0 bg-paper">
                        <div className="font-mono text-[12px] font-semibold text-accent">{r.code}</div>
                        <div className="text-[11.5px] text-txt3">{r.statement}</div>
                      </Td>
                      {xwalk.data!.columns.map((c) => {
                        const pts = r.cells[c.id] ?? [];
                        return (
                          <Td key={c.id}>
                            {pts.length === 0
                              ? <span className="font-mono text-txt3">—</span>
                              : pts.map((p, i) => (
                                <span key={i} className="mr-1 inline-block rounded bg-ok-bg px-1.5 py-[1px] font-mono text-[11px] font-semibold text-ok">{p}</span>
                              ))}
                          </Td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {openId && <ControlDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}
