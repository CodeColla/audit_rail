import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { Card, Loading, PageHead, Pill, Table, Td } from "../lib/ui";

type Assessment = { id: string; bank_name: string; title: string; status: string; predicted_verdict: string | null };

export default function Reports() {
  const { data, isLoading } = useQuery({ queryKey: ["assessments"], queryFn: () => get<Assessment[]>("/assessments") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const closed = rows.filter((r) => ["verdict_issued", "closed", "submitted"].includes(r.status));
  return (
    <>
      <PageHead eyebrow="Reports" title="Reports" lead="Verdict history and coverage — the story you tell leadership between audits." />
      <div className="mb-7 grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card><div className="eyebrow mb-2">Assessments</div><div className="text-display font-semibold tnum">{rows.length}</div></Card>
        <Card><div className="eyebrow mb-2">Submitted / closed</div><div className="text-display font-semibold tnum">{closed.length}</div></Card>
        <Card><div className="eyebrow mb-2">In progress</div><div className="text-display font-semibold tnum text-accent">{rows.length - closed.length}</div></Card>
      </div>
      <div className="mb-3"><h2 className="text-body font-semibold">Verdict history</h2></div>
      {rows.length === 0 ? <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">No assessments yet.</div> : (
        <Table head={["Bank", "Assessment", "Status", "Predicted verdict"]}>
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-canvas">
              <Td className="font-medium">{r.bank_name}</Td>
              <Td>{r.title}</Td>
              <Td><Pill tone={r.status}>{r.status.replace(/_/g, " ")}</Pill></Td>
              <Td>{r.predicted_verdict ? <Pill tone={r.predicted_verdict}>{r.predicted_verdict}</Pill> : "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
