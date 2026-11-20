import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { DataTable } from "../../components/DataTable";
import { Bar, inputCls, Loading, PageHead, Pill } from "../../lib/ui";

type Assessment = {
  id: string; title: string; bank_name: string; status: string; predicted_verdict: string | null;
  total_questions: number; answered: number; bank_spoc_name: string | null;
};

export default function Audits() {
  const nav = useNavigate();
  const can = useCan();
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["assessments", q],
    queryFn: () => get<Assessment[]>(`/assessments${q ? `?q=${encodeURIComponent(q)}` : ""}`),
    placeholderData: keepPreviousData,
  });
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
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search bank or title…"
        aria-label="Search audits" className={inputCls + " mb-4 max-w-xs"} />
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          {q ? `No audits match "${q}".` : "No assessments yet. Create one from an imported template."}
        </div>
      ) : (
        // #20 S6. Client-side sort over the fetched rows. No onSearch or onDeleteOne: the search
        // box above is the server query, and audits are not bulk-deleted from this list.
        <DataTable<Assessment>
          rows={rows}
          getId={(a) => a.id}
          onRowClick={(a) => nav(`/audits/${a.id}`)}
          columns={[
            {
              key: "bank", label: "Bank / assessment",
              sortValue: (a) => a.bank_name,
              render: (a) => <><div className="font-medium">{a.bank_name}</div>
                <div className="text-caption text-txt3">{a.title}</div></>,
            },
            {
              key: "progress", label: "Progress", className: "min-w-[150px]",
              sortValue: (a) => (a.total_questions ? a.answered / a.total_questions : 0),
              render: (a) => {
                const pct = a.total_questions ? Math.round((a.answered / a.total_questions) * 100) : 0;
                return <><Bar pct={pct} muted={a.status === "submitted"} />
                  <div className="mt-1 text-caption text-txt3 tnum">{a.answered} / {a.total_questions} answered</div></>;
              },
            },
            {
              key: "status", label: "Status",
              sortValue: (a) => a.status,
              render: (a) => <Pill tone={a.status}>{a.status.replace(/_/g, " ")}</Pill>,
            },
            {
              key: "verdict", label: "Predicted verdict",
              sortValue: (a) => a.predicted_verdict,
              render: (a) => a.predicted_verdict
                ? <Pill tone={a.predicted_verdict}>{a.predicted_verdict}</Pill> : "—",
            },
            {
              key: "spoc", label: "Bank SPOC",
              sortValue: (a) => a.bank_spoc_name,
              render: (a) => a.bank_spoc_name ?? "—",
            },
          ]}
        />
      )}
    </>
  );
}
