import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { Bar, Loading, PageHead, Pill, Table, Td } from "../../lib/ui";

type Assessment = {
  id: string; title: string; bank_name: string; status: string; predicted_verdict: string | null;
  total_questions: number; answered: number; bank_spoc_name: string | null;
};

export default function Audits() {
  const nav = useNavigate();
  const can = useCan();
  const { data, isLoading } = useQuery({ queryKey: ["assessments"], queryFn: () => get<Assessment[]>("/assessments") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Assessments" title="Audits"
        lead="Every bank audit as a live workspace: import a checklist, pre-fill from your control library, chase evidence, export back."
        action={can("audits", "add")
          ? <div className="flex gap-2">
              {/* P5-S10: the crosswalk review used to be reachable only in the seconds after
                  an import finished, which is how 667 proposals went unreviewed. */}
              <Link to="/mappings" className="btn">Mappings</Link>
              <Link to="/audits/import" className="btn btn-primary">Import checklist</Link>
            </div>
          : undefined} />
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          No assessments yet. Create one from an imported template.
        </div>
      ) : (
        <Table head={["Bank / assessment", "Progress", "Status", "Predicted verdict", "Bank SPOC"]}>
          {rows.map((a) => {
            const pct = a.total_questions ? Math.round((a.answered / a.total_questions) * 100) : 0;
            return (
              <tr key={a.id} className="cursor-pointer hover:bg-canvas" onClick={() => nav(`/audits/${a.id}`)}>
                <Td><div className="font-medium">{a.bank_name}</div>
                  <div className="text-caption text-txt3">{a.title}</div></Td>
                <Td className="min-w-[150px]"><Bar pct={pct} muted={a.status === "submitted"} />
                  <div className="mt-1 text-caption text-txt3 tnum">{a.answered} / {a.total_questions} answered</div></Td>
                <Td><Pill tone={a.status}>{a.status.replace(/_/g, " ")}</Pill></Td>
                <Td>{a.predicted_verdict ? <Pill tone={a.predicted_verdict}>{a.predicted_verdict}</Pill> : "—"}</Td>
                <Td>{a.bank_spoc_name ?? "—"}</Td>
              </tr>
            );
          })}
        </Table>
      )}
    </>
  );
}
