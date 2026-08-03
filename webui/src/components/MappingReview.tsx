import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText } from "../lib/api";
import { ControlPicker } from "./ControlPicker";
import { Bar, cn, Loading, Pill } from "../lib/ui";

/**
 * Review the proposed crosswalk between a bank's checklist questions and our controls.
 *
 * Extracted out of `Import.tsx` in P5-S10, unchanged in behaviour. It had lived inside the
 * import wizard, gated on the in-memory result of an import that had *just* happened — so the
 * moment you navigated away there was no route back to it, and unreviewed proposals piled up
 * invisibly (667 of them at ~0.30 average confidence on this install). The API
 * (`GET/POST /templates/{id}/proposals`) was always complete; only the way in was missing.
 *
 * The crosswalk is what makes "answer once, reuse everywhere" true, and a mapping nobody
 * checked is a wrong answer waiting to be reused — so this is the screen that decides whether
 * the whole premise holds.
 */

export type Proposal = {
  question_id: string; number: string | null; text: string;
  control_id: string | null; code: string | null; statement: string | null;
  confidence: number | null; status: string;
};

export function MappingReview({ templateId, onChanged }:
  { templateId: string; onChanged?: () => void }) {
  const qc = useQueryClient();
  const [remapping, setRemapping] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [onlyPending, setOnlyPending] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["proposals", templateId],
    queryFn: () => api.get<Proposal[]>(`/templates/${templateId}/proposals`)
      .then((r) => r.data),
  });
  const proposals = data ?? [];

  const refresh = () => {
    // A re-map changes the row's code and statement too, so the list is re-fetched rather
    // than patched in place.
    qc.invalidateQueries({ queryKey: ["proposals", templateId] });
    qc.invalidateQueries({ queryKey: ["templates"] });   // the backlog badge on Audits
    onChanged?.();
  };

  async function decide(question_id: string, action: "confirm" | "reject", control_id?: string) {
    try {
      setErr("");
      await api.post(`/templates/${templateId}/proposals/confirm`,
        { decisions: [{ question_id, action, ...(control_id ? { control_id } : {}) }] });
      refresh();
    } catch (e: any) {
      setErr(errText(e, "Could not save that decision."));
    }
  }

  async function confirmAll(threshold: number) {
    try {
      setErr("");
      await api.post(`/templates/${templateId}/proposals/confirm`,
        { confirm_high_confidence: threshold });
      refresh();
    } catch (e: any) {
      setErr(errText(e, "Could not confirm those mappings."));
    }
  }

  if (isLoading) return <Loading />;

  const confirmed = proposals.filter((p) => p.status === "confirmed").length;
  const pending = proposals.filter((p) => p.status === "suggested").length;
  const rows = onlyPending ? proposals.filter((p) => p.status === "suggested") : proposals;

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Pill tone="ok">{confirmed} confirmed</Pill>
        <Pill tone={pending ? "warn" : "na"}>{pending} to review</Pill>
        <button onClick={() => setOnlyPending((s) => !s)}
          className="text-[12px] font-medium text-accent hover:underline">
          {onlyPending ? "Show all" : "Show only unreviewed"}
        </button>
        <div className="ml-auto flex gap-2">
          {/* Two thresholds, because one is not a policy. ≥50% is the "obviously right"
              sweep; ≥30% still needs eyes but clears the long tail faster than one at a
              time. Anything lower is guessing, and a wrong confirmed mapping silently
              prefills the wrong answer into a real audit. */}
          <button onClick={() => confirmAll(0.5)} className="btn">Confirm all ≥ 50%</button>
          <button onClick={() => confirmAll(0.3)} className="btn">≥ 30%</button>
        </div>
      </div>
      {err && <div role="alert" className="mb-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}

      <div className="max-h-[65vh] overflow-y-auto rounded-xl border border-bd bg-paper">
        <table className="w-full text-[13px]">
          <thead className="sticky top-0">
            <tr>
              {["#", "Bank question", "Proposed standard control", "Confidence", ""].map((h, i) => (
                <th key={i} className="border-b border-bd bg-canvas px-3.5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wider text-txt3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => {
              const c = Math.round((p.confidence ?? 0) * 100);
              return (
                <Fragment key={p.question_id}>
                <tr className={cn("border-b border-bd", p.status === "rejected" && "opacity-40")}>
                  <td className="px-3.5 py-2.5 font-mono text-txt3">#{p.number}</td>
                  <td className="px-3.5 py-2.5">{p.text}</td>
                  <td className="px-3.5 py-2.5">
                    {p.code ? <><span className="font-mono font-semibold text-accent">{p.code}</span>
                      <span className="ml-2 text-txt3">{p.statement}</span></> : <span className="text-txt3">no match</span>}
                  </td>
                  <td className="w-32 px-3.5 py-2.5">
                    <Bar pct={c} muted={c < 30} /><div className="mt-1 text-[11px] text-txt3 tnum">{c}%</div>
                  </td>
                  <td className="whitespace-nowrap px-3.5 py-2.5">
                    <div className="flex items-center gap-1.5">
                      {p.status === "suggested" ? (
                        <>
                          <button onClick={() => decide(p.question_id, "confirm")}
                            className="grid h-7 w-7 place-items-center rounded-md border border-ok/40 text-ok hover:bg-ok-bg"
                            aria-label={`Confirm mapping for question ${p.number}`} title="Confirm">✓</button>
                          <button onClick={() => decide(p.question_id, "reject")}
                            className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad"
                            aria-label={`Reject mapping for question ${p.number}`} title="Reject">✕</button>
                        </>
                      ) : <Pill tone={p.status === "confirmed" ? "ok" : "na"}>{p.status}</Pill>}
                      {/* Re-map is offered regardless of status — a CONFIRMED proposal can
                          still be pointed at the wrong control and needs fixing too, not
                          just a suggested one. */}
                      <button onClick={() => setRemapping(remapping === p.question_id ? null : p.question_id)}
                        className="rounded-md border border-bd px-2 py-1 text-[11px] text-txt2 hover:bg-canvas">
                        Re-map
                      </button>
                    </div>
                  </td>
                </tr>
                {remapping === p.question_id && (
                  <tr className="border-b border-bd bg-canvas/40">
                    <td colSpan={5} className="px-3.5 py-2.5">
                      <ControlPicker
                        onClose={() => setRemapping(null)}
                        onPick={(ctl) => { setRemapping(null); decide(p.question_id, "confirm", ctl.id); }} />
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
            {rows.length === 0 && (
              <tr><td colSpan={5} className="px-3.5 py-6 text-center text-[12.5px] text-txt3">
                Every mapping on this checklist has been reviewed.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
