import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../lib/api";
import { inputCls, cn, Card, Drawer, Loading, Modal, PageHead, Pill, Table, Td } from "../lib/ui";
import { useCan } from "../lib/auth";
import { OwnerSelect } from "./Registers";

type Task = {
  id: string; title: string; description: string | null;
  cadence_months: number | null; frequency: string | null; interval_count: number | null;
  next_due_at: string | null; status: string; assignee_person_id: string | null;
  risk_id: string | null;
  run_status: string; next_run: { id: string } | null;
};
type Run = { id: string; due_at: string; status: string; notes: string | null };
type TaskDetail = Task & { runs: Run[] };
type CalendarRun = { id: string; due_at: string; status: string; title: string; task_id: string };
type Ev = { id: string; title: string };
type Risk = { id: string; title: string };

const FREQUENCIES = ["DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"] as const;
const STATUS_TABS = ["active", "paused", "completed", "all"] as const;
const nice = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

const UNIT_ONE: Record<string, string> = { DAILY: "day", WEEKLY: "week", MONTHLY: "month", QUARTERLY: "quarter", YEARLY: "year" };
const UNIT_MANY: Record<string, string> = { DAILY: "days", WEEKLY: "weeks", MONTHLY: "months", QUARTERLY: "quarters", YEARLY: "years" };

/** "Every 2 weeks", "Every quarter", "Every 1 month" — cadence_months is the legacy
 * control-generated shape, never editable here; frequency/interval is what a
 * hand-created task uses. Only one of the two is ever set (the API enforces it). */
function cadenceLabel(t: Pick<Task, "cadence_months" | "frequency" | "interval_count">): string {
  if (t.frequency && t.interval_count) {
    return t.interval_count === 1
      ? `Every ${UNIT_ONE[t.frequency]}`
      : `Every ${t.interval_count} ${UNIT_MANY[t.frequency]}`;
  }
  if (t.cadence_months) return `Every ${t.cadence_months} mo`;
  return "One-off";
}

/** Shared by create and edit — recurrence, assignee, and the optional risk link. */
function RecurrenceFields({ f, set }: {
  f: { frequency: string; interval_count: string; assignee_person_id: string; risk_id: string };
  set: (k: string) => (v: string) => void;
}) {
  const risks = useQuery({ queryKey: ["risks"], queryFn: () => get<Risk[]>("/risks") });
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-[13px] font-medium">Recurrence
          <select value={f.frequency} onChange={(e) => set("frequency")(e.target.value)}
            className={inputCls + " mt-1"}>
            <option value="">One-off</option>
            {FREQUENCIES.map((fr) => <option key={fr} value={fr}>{nice(fr)}</option>)}
          </select></label>
        {f.frequency && (
          <label className="text-[13px] font-medium">Every
            <input type="number" min={1} value={f.interval_count}
              onChange={(e) => set("interval_count")(e.target.value)}
              className={inputCls + " mt-1"} placeholder="1" /></label>)}
        <label className="text-[13px] font-medium">Assignee
          <OwnerSelect value={f.assignee_person_id} onChange={set("assignee_person_id")} /></label>
      </div>
      <label className="text-[13px] font-medium">Related risk <span className="text-txt3">(optional)</span>
        <select value={f.risk_id} onChange={(e) => set("risk_id")(e.target.value)} className={inputCls + " mt-1"}>
          <option value="">— none —</option>
          {(risks.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
        </select></label>
    </>
  );
}

function NewTaskModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ title: "", description: "", frequency: "", interval_count: "1",
    next_due_at: "", assignee_person_id: "", risk_id: "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const create = useMutation({
    mutationFn: () => api.post("/tasks", {
      title: f.title, description: f.description || null,
      frequency: f.frequency || null, interval_count: f.frequency ? +f.interval_count : null,
      next_due_at: f.next_due_at || null,
      assignee_person_id: f.assignee_person_id || null, risk_id: f.risk_id || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tasks"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title.trim()) create.mutate(); };
  return (
    <Modal open onClose={onClose} title="New task" size="lg">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} placeholder="Quarterly access review" /></label>
        <label className="text-[13px] font-medium">Description
          <textarea value={f.description} onChange={(e) => set("description")(e.target.value)}
            className={inputCls + " mt-1 min-h-[56px]"} /></label>
        <RecurrenceFields f={f} set={set} />
        <label className="text-[13px] font-medium">{f.frequency ? "First due" : "Due"}
          <input type="date" value={f.next_due_at} onChange={(e) => set("next_due_at")(e.target.value)}
            className={inputCls + " mt-1"} /></label>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.title.trim() || create.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create task"}</button>
      </form>
    </Modal>
  );
}

function EditTaskModal({ task, onClose }: { task: TaskDetail; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ title: task.title, description: task.description ?? "",
    frequency: task.frequency ?? "", interval_count: String(task.interval_count ?? "1"),
    assignee_person_id: task.assignee_person_id ?? "", risk_id: task.risk_id ?? "" });
  const [err, setErr] = useState("");
  const set = (k: string) => (v: string) => setF({ ...f, [k]: v });
  const save = useMutation({
    mutationFn: () => api.patch(`/tasks/${task.id}`, {
      title: f.title, description: f.description || null,
      frequency: f.frequency || null, interval_count: f.frequency ? +f.interval_count : null,
      assignee_person_id: f.assignee_person_id || null, risk_id: f.risk_id || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["task", task.id] });
      onClose();
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title.trim()) save.mutate(); };
  return (
    <Modal open onClose={onClose} title="Edit task" size="lg">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-[13px] font-medium">Title *
          <input required value={f.title} onChange={(e) => set("title")(e.target.value)}
            className={inputCls + " mt-1"} /></label>
        <label className="text-[13px] font-medium">Description
          <textarea value={f.description} onChange={(e) => set("description")(e.target.value)}
            className={inputCls + " mt-1 min-h-[56px]"} /></label>
        <RecurrenceFields f={f} set={set} />
        <p className="text-[11px] text-txt3">
          The next run's due date isn't editable here — complete the current run to roll it forward, or pause the task instead.</p>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <button disabled={!f.title.trim() || save.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {save.isPending ? "Saving…" : "Save"}</button>
      </form>
    </Modal>
  );
}

function CompleteModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const qc = useQueryClient();
  const [evId, setEvId] = useState(""); const [notes, setNotes] = useState("");
  // A native <select>; no search box to feed, so it holds the unfiltered slot of the key.
  const evList = useQuery({ queryKey: ["evidence", ""], queryFn: () => get<Ev[]>("/evidence") });
  const complete = useMutation({
    mutationFn: () => api.post(`/tasks/${task.id}/runs/${task.next_run!.id}/complete`,
      { evidence_id: evId || null, notes: notes || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["task", task.id] });
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

/** The list row's inline shortcut — the drawer offers the same action, this just saves a
 * click for the common case of "I did the thing, mark it done". */
function CompleteButton({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} className="btn py-1.5">Complete</button>
      {open && <CompleteModal task={task} onClose={() => setOpen(false)} />}
    </>
  );
}

function TaskDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const can = useCan();
  const [editing, setEditing] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [err, setErr] = useState("");
  const { data: task } = useQuery({ queryKey: ["task", id], queryFn: () => get<TaskDetail>(`/tasks/${id}`) });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["task", id] });
  };
  const pause = useMutation({ mutationFn: () => api.post(`/tasks/${id}/pause`, {}),
    onSuccess: () => { setErr(""); refresh(); },
    onError: (e: any) => setErr(errText(e, "Could not pause.")) });
  const resume = useMutation({ mutationFn: () => api.post(`/tasks/${id}/resume`, {}),
    onSuccess: () => { setErr(""); refresh(); },
    onError: (e: any) => setErr(errText(e, "Could not resume.")) });
  const del = useMutation({
    mutationFn: () => api.delete(`/tasks/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tasks"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not delete.")),
  });

  if (!task) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`TASK · ${cadenceLabel(task)}`} title={task.title}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={task.status}>{nice(task.status)}</Pill>
        {task.next_run && <Pill tone={task.run_status}>{nice(task.run_status)}</Pill>}
        {can("tasks", "edit") && !editing && (
          <button onClick={() => setEditing(true)} className="text-[12px] font-medium text-accent">Edit</button>)}
      </div>
      {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
      {task.description && <p className="text-[13px] text-txt2">{task.description}</p>}

      <Card>
        <div className="eyebrow mb-2">Run history</div>
        {task.runs.length === 0 ? <p className="text-[12.5px] text-txt3">No runs yet.</p> : (
          <div className="flex flex-col gap-1.5">
            {task.runs.map((r) => (
              <div key={r.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
                <span className="font-mono text-txt2">{r.due_at.slice(0, 10)}</span>
                <Pill tone={r.status}>{nice(r.status)}</Pill>
                {r.notes && <span className="min-w-0 flex-1 truncate text-txt3">{r.notes}</span>}
              </div>))}
          </div>)}
      </Card>

      <div className="flex flex-wrap gap-2">
        {task.next_run && can("tasks", "edit") && (
          <button onClick={() => setCompleting(true)} className="btn btn-primary py-1.5">Complete</button>)}
        {can("tasks", "edit") && task.status === "active" && (
          <button onClick={() => pause.mutate()} disabled={pause.isPending} className="btn py-1.5 disabled:opacity-50">Pause</button>)}
        {can("tasks", "edit") && task.status === "paused" && (
          <button onClick={() => resume.mutate()} disabled={resume.isPending} className="btn py-1.5 disabled:opacity-50">Resume</button>)}
        {can("tasks", "delete") && (
          <button onClick={() => { if (confirm(`Delete "${task.title}"? This cannot be undone.`)) del.mutate(); }}
            disabled={del.isPending} className="btn py-1.5 text-bad hover:border-bad disabled:opacity-50">Delete</button>)}
      </div>

      {editing && <EditTaskModal task={task} onClose={() => setEditing(false)} />}
      {completing && task.next_run && <CompleteModal task={task} onClose={() => setCompleting(false)} />}
    </Drawer>
  );
}

function TaskList() {
  const can = useCan();
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [status, setStatus] = useState<typeof STATUS_TABS[number]>("active");
  const { data, isLoading } = useQuery({
    queryKey: ["tasks", status], queryFn: () => get<Task[]>(`/tasks?status=${status}`) });
  const rows = data ?? [];

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          {STATUS_TABS.map((s) => (
            <button key={s} onClick={() => setStatus(s)}
              className={cn("rounded-full px-3 py-1 text-[12px] font-medium capitalize",
                status === s ? "bg-ink text-paper" : "bg-canvas text-txt2 hover:bg-hair")}>
              {s}</button>))}
        </div>
        {can("tasks", "add") && (
          <button onClick={() => setAdding(true)} className="btn btn-primary">＋ New task</button>)}
      </div>
      {isLoading ? <Loading /> : rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          {status === "active" ? "No active tasks. Recurring controls generate their own; create one directly for anything else."
            : `No ${status} tasks.`}
        </div>
      ) : (
        <Table head={["Task", "Recurrence", "Next due", "Status", ""]}>
          {rows.map((t) => (
            <tr key={t.id} className="cursor-pointer hover:bg-canvas" onClick={() => setOpenId(t.id)}>
              <Td className="font-medium">{t.title}</Td>
              <Td className="text-txt2">{cadenceLabel(t)}</Td>
              <Td className="font-mono text-txt2">{t.next_due_at?.slice(0, 10) ?? "—"}</Td>
              <Td><Pill tone={t.status === "active" ? t.run_status : t.status}>
                {nice(t.status === "active" ? t.run_status : t.status)}</Pill></Td>
              <Td><span onClick={(e) => e.stopPropagation()}>
                {t.next_run && t.status === "active" && <CompleteButton task={t} />}</span></Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewTaskModal onClose={() => setAdding(false)} />}
      {openId && <TaskDrawer id={openId} onClose={() => setOpenId(null)} />}
    </>
  );
}

function CalendarView() {
  const now = new Date();
  const [month, setMonth] = useState(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
  const { data, isLoading } = useQuery({
    queryKey: ["tasks-calendar", month], queryFn: () => get<CalendarRun[]>(`/tasks/calendar?month=${month}`) });
  const shift = (delta: number) => {
    const [y, m] = month.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    setMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
  };
  const monthLabel = new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const rows = data ?? [];
  const byDay = new Map<string, CalendarRun[]>();
  for (const r of rows) {
    const day = r.due_at.slice(0, 10);
    byDay.set(day, [...(byDay.get(day) ?? []), r]);
  }
  const days = [...byDay.keys()].sort();

  return (
    <>
      <div className="mb-4 flex items-center gap-3">
        <button onClick={() => shift(-1)} className="btn py-1.5 px-2.5">←</button>
        <div className="text-[14px] font-semibold">{monthLabel}</div>
        <button onClick={() => shift(1)} className="btn py-1.5 px-2.5">→</button>
      </div>
      {isLoading ? <Loading /> : days.length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-8 text-center text-sm text-txt3">
          Nothing due in {monthLabel}.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {days.map((day) => (
            <Card key={day}>
              <div className="eyebrow mb-2">{new Date(`${day}T00:00:00`).toLocaleDateString(undefined,
                { weekday: "long", day: "numeric", month: "long" })}</div>
              <div className="flex flex-col gap-1.5">
                {(byDay.get(day) ?? []).map((r) => (
                  <div key={r.id} className="flex items-center gap-2 border-t border-bd py-1.5 text-[12.5px] first:border-t-0">
                    <span className="min-w-0 flex-1 truncate font-medium">{r.title}</span>
                    <Pill tone={r.status}>{nice(r.status)}</Pill>
                  </div>))}
              </div>
            </Card>))}
        </div>
      )}
    </>
  );
}

export default function Tasks() {
  const [tab, setTab] = useState<"list" | "calendar">("list");
  return (
    <>
      <PageHead eyebrow="Compliance calendar" title="Tasks"
        lead="Recurring obligations generate dated tasks. Completing one attaches the artifact it produces — turning cadence into evidence." />
      <div className="mb-5 flex gap-1 border-b border-bd">
        {(["list", "calendar"] as const).map((tt) => (
          <button key={tt} onClick={() => setTab(tt)}
            className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-[13px] font-medium capitalize",
              tab === tt ? "border-accent text-ink" : "border-transparent text-txt2")}>
            {tt}</button>))}
      </div>
      {tab === "list" ? <TaskList /> : <CalendarView />}
    </>
  );
}
