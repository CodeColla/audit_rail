import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, get } from "../lib/api";
import { inputCls, Loading, Modal, PageHead, Pill, Table, Td } from "../lib/ui";

type Task = {
  id: string; title: string; cadence_months: number | null; next_due_at: string | null;
  run_status: string; next_run: { id: string } | null;
};
type Ev = { id: string; title: string };

function CompleteModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const qc = useQueryClient();
  const [evId, setEvId] = useState(""); const [notes, setNotes] = useState("");
  const evList = useQuery({ queryKey: ["evidence"], queryFn: () => get<Ev[]>("/evidence") });
  const complete = useMutation({
    mutationFn: () => api.post(`/tasks/${task.id}/runs/${task.next_run!.id}/complete`,
      { evidence_id: evId || null, notes: notes || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
  });
  return (
    <Modal open onClose={onClose} title="Complete task">
      <p className="mb-3 text-[13px] font-medium">{task.title}</p>
      <label className="text-[13px] font-medium">Attach produced evidence <span className="text-txt3">(optional)</span>
        <select value={evId} onChange={(e) => setEvId(e.target.value)} className={inputCls + " mt-1"}>
          <option value="">— none —</option>
          {(evList.data ?? []).map((e) => <option key={e.id} value={e.id}>{e.title}</option>)}
        </select>
      </label>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes…" className={inputCls + " mt-3 min-h-[60px]"} />
      <button disabled={complete.isPending} onClick={() => complete.mutate()} className="btn btn-primary mt-3 w-full justify-center disabled:opacity-50">
        {complete.isPending ? "Completing…" : "Mark complete & roll forward"}
      </button>
    </Modal>
  );
}

export default function Tasks() {
  const [active, setActive] = useState<Task | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["tasks"], queryFn: () => get<Task[]>("/tasks") });
  if (isLoading) return <Loading />;
  const rows = data ?? [];

  return (
    <>
      <PageHead eyebrow="Compliance calendar" title="Tasks"
        lead="Recurring obligations generate dated tasks. Completing one attaches the artifact it produces — turning cadence into evidence." />
      {rows.length === 0 ? <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">No tasks. They generate from recurring controls.</div> : (
        <Table head={["Task", "Cadence", "Next due", "Status", ""]}>
          {rows.map((t) => (
            <tr key={t.id} className="hover:bg-canvas">
              <Td className="font-medium">{t.title}</Td>
              <Td className="text-txt2">{t.cadence_months ? `Every ${t.cadence_months} mo` : "One-off"}</Td>
              <Td className="font-mono text-txt2">{t.next_due_at ?? "—"}</Td>
              <Td><Pill tone={t.run_status}>{t.run_status.replace(/_/g, " ")}</Pill></Td>
              <Td>{t.next_run && (
                <button onClick={() => setActive(t)} className="btn py-1.5">Complete</button>
              )}</Td>
            </tr>
          ))}
        </Table>
      )}
      {active && <CompleteModal task={active} onClose={() => setActive(null)} />}
    </>
  );
}
