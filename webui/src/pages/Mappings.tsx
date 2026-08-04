import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { MappingReview } from "../components/MappingReview";
import { Card, Loading, PageHead, Pill } from "../lib/ui";

/**
 * The crosswalk backlog (P5-S10).
 *
 * Every imported checklist auto-proposes a mapping from each bank question onto one of our
 * controls, scored by confidence, for a human to confirm. That review only ever existed as a
 * step *inside* the import wizard — so once you left the page, the remaining proposals were
 * unreachable. They accumulated silently: 667 unconfirmed at ~0.30 average confidence.
 *
 * This matters more than a tidy-up. `question_control_map` is what makes "answer once, reuse
 * everywhere" work — an unreviewed mapping means a bank's question is quietly pointed at a
 * control nobody agreed it belongs to, and its stock answer gets prefilled into a real audit
 * response. The backlog is unverified answers, not just unfinished admin.
 */

type Template = {
  id: string; bank_name: string; version_label: string | null;
  question_count: number; unconfirmed_mappings: number;
};

export default function Mappings() {
  const { id } = useParams();
  const { data, isLoading } = useQuery({
    queryKey: ["templates"], queryFn: () => get<Template[]>("/templates") });

  if (isLoading) return <Loading />;
  const templates = data ?? [];

  if (id) {
    const tpl = templates.find((t) => t.id === id);
    return (
      <>
        <PageHead eyebrow="Audits · Crosswalk"
          title={tpl ? `${tpl.bank_name}${tpl.version_label ? ` ${tpl.version_label}` : ""}` : "Mappings"}
          lead="Each of this checklist's questions is proposed onto one of your standard controls. Confirming a mapping is what lets that control's answer and evidence serve this bank's audit."
          action={<Link to="/mappings" className="btn">← All checklists</Link>} />
        <MappingReview templateId={id} />
      </>
    );
  }

  const backlog = templates.reduce((n, t) => n + t.unconfirmed_mappings, 0);

  return (
    <>
      <PageHead eyebrow="Audits · Crosswalk" title="Checklist mappings"
        lead="Which bank question means which of your controls. Proposed automatically on import, confirmed by you — an unreviewed mapping will still prefill an audit answer, so it is worth the pass."
        action={<Link to="/audits" className="btn">Audits</Link>} />

      {backlog > 0 && (
        <Card className="mb-4 border-l-[3px] border-l-warn">
          <div className="text-sm">
            <b className="tnum">{backlog}</b> proposed mapping{backlog === 1 ? "" : "s"} across
            all checklists {backlog === 1 ? "has" : "have"} never been reviewed. Each one points
            a bank's question at a control nobody has agreed it belongs to.
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.map((t) => (
          <Card key={t.id}>
            <Link to={`/mappings/${t.id}`} className="text-body font-semibold hover:underline">
              {t.bank_name}
            </Link>
            <div className="text-caption text-txt3">
              {t.version_label || "no version label"} · {t.question_count} questions
            </div>
            <div className="mt-3">
              {t.unconfirmed_mappings > 0
                ? <Pill tone="warn">{t.unconfirmed_mappings} to review</Pill>
                : <Pill tone="ok">all reviewed</Pill>}
            </div>
          </Card>
        ))}
      </div>

      {templates.length === 0 && (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center">
          <h3 className="text-body font-semibold">No checklists imported yet</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-sm text-txt2">
            Import a bank's questionnaire and its questions are mapped onto your controls
            automatically, ready for you to confirm.
          </p>
          <Link to="/audits/import" className="btn btn-primary mt-4">Import a checklist</Link>
        </div>
      )}
    </>
  );
}
