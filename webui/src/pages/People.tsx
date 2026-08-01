import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, errText, get } from "../lib/api";
import { useCan } from "../lib/auth";
import { LookupSelect } from "./Registers";
import { Column, DataTable } from "../components/DataTable";
import {
  Card, cn, Drawer, inputCls, Loading, Modal, PageHead, Pill,
} from "../lib/ui";

type Person = {
  id: string; full_name: string; email: string; employee_number: string | null;
  department: string | null; position: string | null; manager_id: string | null;
  contract_start_date: string | null; contract_end_date: string | null;
  state: string; source: string; user_id: string | null; effective_state: string;
};
type Detail = Person & {
  manager: { id: string; full_name: string } | null;
  reports: { id: string; full_name: string; position: string | null }[];
  has_login: boolean;
};

const initials = (n: string) =>
  n.split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

const daysUntil = (d: string | null) =>
  d ? Math.ceil((new Date(d.slice(0, 10)).getTime() - Date.now()) / 86400000) : null;

/** Status = state + contract dates. `Ends 14d` warns; `Inactive` is neutral, not an error. */
function StatusPill({ p }: { p: Person }) {
  if (p.effective_state === "INACTIVE")
    return <span title="Auto-inactive: contract ended. Cannot be assigned owners or approvals."><Pill tone="na">Inactive</Pill></span>;
  const d = daysUntil(p.contract_end_date);
  if (d !== null && d <= 30)
    return <span title={`Contract ends ${p.contract_end_date?.slice(0, 10)}`}><Pill tone="warn">Ends {d}d</Pill></span>;
  return <Pill tone="ok">Active</Pill>;
}

/** The teaching device: "no login" is the NORMAL state for most staff — never amber. */
function AccessPill({ p }: { p: Person }) {
  return p.user_id
    ? <span title="Has an account and can sign in."><Pill tone="info">Login</Pill></span>
    : <span title="Signs policies by emailed link. No account needed."><Pill tone="na">No login</Pill></span>;
}

function PersonForm({ onClose, editing }: { onClose: () => void; editing?: Detail }) {
  const qc = useQueryClient();
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const [f, setF] = useState({
    full_name: editing?.full_name ?? "", email: editing?.email ?? "",
    employee_number: editing?.employee_number ?? "", department: editing?.department ?? "",
    position: editing?.position ?? "", manager_id: editing?.manager_id ?? "",
    contract_start_date: editing?.contract_start_date?.slice(0, 10) ?? "",
    contract_end_date: editing?.contract_end_date?.slice(0, 10) ?? "",
  });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const save = useMutation({
    mutationFn: () => {
      const body: any = Object.fromEntries(
        Object.entries(f).map(([k, v]) => [k, v === "" ? null : v]));
      if (editing) { delete body.email; return api.patch(`/people/${editing.id}`, body); }
      return api.post("/people", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["person"] });
      qc.invalidateQueries({ queryKey: ["departments"] });
      onClose();
    },
    onError: (e: any) => setErr(errText(e, "Could not save.")),
  });

  const submit = (e: FormEvent) => { e.preventDefault(); setErr(""); save.mutate(); };

  return (
    <Modal open onClose={onClose} title={editing ? "Edit person" : "Add person"}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="text-[13px] font-medium">Full name *
            <input required value={f.full_name} onChange={set("full_name")} className={inputCls + " mt-1"} />
          </label>
          <label className="text-[13px] font-medium">Email *
            <input required type="email" value={f.email} onChange={set("email")}
              disabled={!!editing} className={cn(inputCls, "mt-1", editing && "opacity-60")} />
          </label>
          <label className="text-[13px] font-medium">Employee number
            <input value={f.employee_number} onChange={set("employee_number")} className={inputCls + " mt-1"} />
          </label>
          {/* P5-S6: these were an <input list="dept-list"> whose datalist was built from
              departments ALREADY IN USE, and a bare text box. The first is what Sumit
              reported as "Department can't be extended" — it looked like a fixed dropdown
              that refused new values, when in fact typing anything always worked. A real
              vocabulary, editable in Masters, makes the affordance match the behaviour. */}
          <label className="text-[13px] font-medium">Department
            <LookupSelect kind="department" value={f.department}
              onChange={(v) => set("department")({ target: { value: v } } as any)} />
          </label>
          <label className="text-[13px] font-medium">Position
            <LookupSelect kind="position" value={f.position}
              onChange={(v) => set("position")({ target: { value: v } } as any)} />
          </label>
          <label className="text-[13px] font-medium">Manager
            <select value={f.manager_id} onChange={set("manager_id")} className={inputCls + " mt-1"}>
              <option value="">— none —</option>
              {(people.data ?? []).filter((p) => p.id !== editing?.id)
                .map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
            </select>
          </label>
          <label className="text-[13px] font-medium">Contract start
            <input type="date" value={f.contract_start_date} onChange={set("contract_start_date")} className={inputCls + " mt-1"} />
          </label>
          <label className="text-[13px] font-medium">Contract end
            <input type="date" value={f.contract_end_date} onChange={set("contract_end_date")} className={inputCls + " mt-1"} />
          </label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
        <p className="rounded-md bg-canvas px-3 py-2 text-[11.5px] text-txt2">
          Adding a person does <b>not</b> create a login. They'll sign policies by emailed link.
          Grant an account from <b>Admin → Members</b> only if they need to sign in.
        </p>
        <button disabled={save.isPending} className="btn btn-primary justify-center disabled:opacity-50">
          {save.isPending ? "Saving…" : editing ? "Save changes" : "Add person"}
        </button>
      </form>
    </Modal>
  );
}

/**
 * Really delete a person — Sumit's decision for P5-S5 — but the API refuses while anything
 * still cites them, naming what blocks it. That 409 is what makes offering this safe: the
 * destructive case (silently orphaning a risk's owner) cannot happen, so the button does not
 * need to hedge. Anyone who has merely LEFT should be marked a leaver instead, which is why
 * the confirm says so rather than just asking "are you sure?".
 */
function DeletePersonButton({ id, name }: { id: string; name: string }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const del = useMutation({
    mutationFn: () => api.delete(`/people/${id}`),
    onSuccess: () => { setErr(""); qc.invalidateQueries({ queryKey: ["people"] }); },
    onError: (e: any) => setErr(errText(e, "Could not delete.")),
  });
  return (
    <>
      <button
        onClick={() => { if (confirm(
            `Delete ${name}?\n\nThis is for a duplicate or a mistake. If they have simply ` +
            `left, set a contract end date instead so their history stays intact.`))
            del.mutate(); }}
        disabled={del.isPending}
        aria-label={`Delete ${name}`}
        className="grid h-7 w-7 place-items-center rounded-md border border-bd text-txt2 hover:border-bad hover:text-bad disabled:opacity-50">
        <Trash2 size={14} />
      </button>
      {err && (
        <div role="alert" className="mt-1 max-w-[22rem] rounded-md bg-bad-bg px-2 py-1 text-[11.5px] text-bad">
          {err}
        </div>)}
    </>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState("");
  const imp = useMutation({
    mutationFn: () => {
      const fd = new FormData(); fd.append("file", file!);
      return api.post("/people/import", fd).then((r) => r.data);
    },
    onSuccess: (d) => { setResult(d); qc.invalidateQueries({ queryKey: ["people"] }); },
    onError: (e: any) => setErr(errText(e, "Import failed.")),
  });

  return (
    <Modal open onClose={onClose} title="Import people">
      {!result ? (
        <>
          <p className="mb-3 text-[12.5px] text-txt2">
            Header row required. Recognised columns:{" "}
            <span className="font-mono text-[11.5px]">full_name, email, employee_number,
            department, position, contract_start_date, contract_end_date</span>.
            Rows that fail are reported back — never silently skipped.
          </p>
          <label className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed border-hair bg-canvas px-4 py-8 text-center">
            <span className="text-[13px] font-medium">{file ? file.name : "Choose a CSV file"}</span>
            <input type="file" accept=".csv" className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          {err && <div className="mt-3 rounded-md bg-bad-bg px-3 py-2 text-[12.5px] text-bad">{err}</div>}
          <button disabled={!file || imp.isPending} onClick={() => imp.mutate()}
            className="btn btn-primary mt-3 w-full justify-center disabled:opacity-50">
            {imp.isPending ? "Importing…" : "Import"}
          </button>
        </>
      ) : (
        <>
          <div className="mb-3 flex gap-2">
            <Pill tone="ok">{result.created} imported</Pill>
            {result.failed > 0 && <Pill tone="bad">{result.failed} failed</Pill>}
          </div>
          {result.errors?.length > 0 && (
            <div className="max-h-56 overflow-y-auto rounded-md border border-bd">
              {result.errors.map((e: any, i: number) => (
                <div key={i} className="border-b border-bd px-3 py-2 text-[12px] last:border-0">
                  <span className="font-mono text-txt3">row {e.row}</span>{" "}
                  <b>{e.name || "(no name)"}</b> — <span className="text-bad">{e.error}</span>
                </div>
              ))}
            </div>
          )}
          <button onClick={onClose} className="btn mt-3 w-full justify-center">Done</button>
        </>
      )}
    </Modal>
  );
}

function PersonDrawer({ id, onClose, onEdit }:
  { id: string; onClose: () => void; onEdit: (d: Detail) => void }) {
  const { data } = useQuery({ queryKey: ["person", id], queryFn: () => get<Detail>(`/people/${id}`) });
  if (!data) return <Drawer open onClose={onClose} title="Loading…"><div /></Drawer>;
  return (
    <Drawer open onClose={onClose} sub={`PERSON · ${data.employee_number ?? "no employee no."}`}
      title={data.full_name}>
      <div className="flex flex-wrap gap-2">
        <AccessPill p={data} /><StatusPill p={data} />
        {data.source === "IMPORT" && <Pill tone="na">imported</Pill>}
      </div>
      <Card>
        <div className="eyebrow mb-2">Details</div>
        {[["Email", data.email], ["Department", data.department], ["Position", data.position],
          ["Manager", data.manager?.full_name], ["Contract start", data.contract_start_date?.slice(0, 10)],
          ["Contract end", data.contract_end_date?.slice(0, 10)]].map(([k, v]) => (
          <div key={k as string} className="flex gap-3 py-1 text-[13px]">
            <span className="w-32 shrink-0 text-txt3">{k}</span>
            <span className="font-medium">{(v as string) || "—"}</span>
          </div>
        ))}
      </Card>
      {!data.has_login && (
        <Card className="border-l-[3px] border-l-na">
          <div className="eyebrow mb-1">No login — this is normal</div>
          <p className="text-[12.5px] text-txt2">
            {data.full_name.split(" ")[0]} doesn't need an account. When a policy needs signing,
            they get a one-time link at <span className="font-mono">{data.email}</span>; the
            signature still records consent, IP and a hash of the exact version signed.
          </p>
        </Card>
      )}
      <Card>
        <div className="eyebrow mb-2">Direct reports ({data.reports.length})</div>
        {data.reports.length === 0
          ? <div className="text-[12.5px] text-txt3">None.</div>
          : data.reports.map((r) => (
            <div key={r.id} className="flex items-center gap-2.5 border-t border-bd py-2 first:border-t-0">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-ink text-[10px] font-bold text-white">{initials(r.full_name)}</span>
              <span className="text-[12.5px] font-medium">{r.full_name}</span>
              <span className="text-[11.5px] text-txt3">{r.position}</span>
            </div>))}
      </Card>
      <button onClick={() => onEdit(data)} className="btn btn-primary self-start">Edit person</button>
    </Drawer>
  );
}

const CHIPS = ["All", "Active", "No login", "Ending soon", "Inactive"] as const;

export default function People() {
  const can = useCan();
  const [chip, setChip] = useState<(typeof CHIPS)[number]>("All");
  const [dept, setDept] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Detail | null>(null);
  const [importing, setImporting] = useState(false);
  const [q, setQ] = useState("");
  const qc = useQueryClient();

  /**
   * TWO queries, deliberately.
   *
   * `["people"]` is a SHARED key — Documents, DocumentDetail and Registers all read it to
   * populate owner pickers. Writing a search-filtered result under it would silently truncate
   * every one of those pickers the moment someone typed here. So the search lives under its
   * own key, and the unfiltered list keeps the shared one.
   *
   * The unfiltered copy also backs `byId` for manager names: a manager who does not match the
   * current search must still resolve, or the column would read "—" and look like missing data.
   * With no search term there is only ONE request, because the list reuses that same result.
   */
  const all = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const searching = q.trim().length > 0;
  const found = useQuery({
    queryKey: ["people", "search", q.trim()],
    queryFn: () => get<Person[]>(`/people?q=${encodeURIComponent(q.trim())}`),
    enabled: searching,
  });
  const depts = useQuery({ queryKey: ["departments"], queryFn: () => get<{ department: string; count: number }[]>("/people/departments") });

  const isLoading = all.isLoading;
  const people = (searching ? found.data : all.data) ?? [];
  const byId = useMemo(
    () => Object.fromEntries((all.data ?? []).map((p) => [p.id, p])), [all.data]);

  const match = (p: Person) => {
    const d = daysUntil(p.contract_end_date);
    if (chip === "Active") return p.effective_state === "ACTIVE";
    if (chip === "No login") return !p.user_id;
    if (chip === "Inactive") return p.effective_state === "INACTIVE";
    if (chip === "Ending soon") return p.effective_state === "ACTIVE" && d !== null && d <= 30;
    return true;
  };
  const count = (c: string) => people.filter((p) => {
    const d = daysUntil(p.contract_end_date);
    if (c === "Active") return p.effective_state === "ACTIVE";
    if (c === "No login") return !p.user_id;
    if (c === "Inactive") return p.effective_state === "INACTIVE";
    if (c === "Ending soon") return p.effective_state === "ACTIVE" && d !== null && d <= 30;
    return true;
  }).length;

  const rows = people.filter(match).filter((p) => !dept || p.department === dept);
  if (isLoading) return <Loading />;

  // Invalidating inside the per-row call is the pattern the other four DataTable pages use.
  // The bare `["people"]` prefix also matches `["people","search",q]`, so both the shared list
  // and the current search refresh; `["departments"]` carries the per-department counts.
  const deletePerson = async (id: string) => {
    await api.delete(`/people/${id}`);
    qc.invalidateQueries({ queryKey: ["people"] });
    qc.invalidateQueries({ queryKey: ["departments"] });
  };

  return (
    <>
      <PageHead eyebrow="Company · People" title="People"
        lead="Everyone in the company — with or without a login. Field engineers never log in; they sign policies by emailed link."
        action={
          <div className="flex gap-2">
            <button onClick={() => setImporting(true)} className="btn">⬆ Import</button>
            {can("people", "add") && (
              <button onClick={() => setAdding(true)} className="btn btn-primary">＋ Add person</button>
            )}
          </div>} />

      {(all.data ?? []).length === 0 ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center">
          <h3 className="text-[15px] font-semibold">Start here — this is the floor of the platform</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-[13px] text-txt2">
            Every policy owner, task assignee and policy signature points at a person. Add your
            team first and everything downstream has somewhere to hang. Most of them will never
            need a login.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <button onClick={() => setImporting(true)} className="btn">Import a roster</button>
            <button onClick={() => setAdding(true)} className="btn btn-primary">＋ Add your first person</button>
          </div>
        </div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {CHIPS.map((c) => (
              <button key={c} onClick={() => setChip(c)}
                className={cn("rounded-full border px-3 py-1.5 text-[12.5px] font-medium",
                  chip === c ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                             : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
                {c} <span className="ml-1 text-txt3 tnum">{count(c)}</span>
              </button>
            ))}
            <select value={dept} onChange={(e) => setDept(e.target.value)}
              className="ml-auto rounded-md border border-bd bg-paper px-2.5 py-1.5 text-[12.5px]">
              <option value="">All departments</option>
              {(depts.data ?? []).map((d) => (
                <option key={d.department} value={d.department}>{d.department} ({d.count})</option>))}
            </select>
          </div>

          <DataTable<Person>
            rows={rows}
            getId={(p) => p.id}
            getRowLabel={(p) => p.full_name}
            onSearch={setQ}
            searchPlaceholder="Search name, email or employee number…"
            onRowClick={(p) => setOpenId(p.id)}
            onDeleteOne={deletePerson}
            canDelete={can("people", "delete")}
            bulkActionCopy={{
              button: "Delete selected", busy: "Deleting…",
              // The per-row confirm has always said this; the bulk one must too, because
              // being refused is the NORMAL outcome for anyone with history behind them.
              confirm: (label) =>
                `Delete ${label}?\n\nThis is for duplicates and mistakes. Anyone still ` +
                `referenced by a risk, task or audit will be refused — if they have simply ` +
                `left, set a contract end date instead so their history stays intact.`,
              errorPrefix: "could not be deleted",
            }}
            emptyMessage="No people match this filter."
            noMatchMessage="Nobody matches that search."
            columns={[
              { key: "name", label: "Name", sortValue: (p) => p.full_name.toLowerCase(),
                render: (p) => (
                  <div className="flex items-center gap-2.5">
                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink text-[10px] font-bold text-white">{initials(p.full_name)}</span>
                    <div>
                      <div className="font-medium">{p.full_name}</div>
                      {p.employee_number && <div className="font-mono text-[11px] text-txt3">{p.employee_number}</div>}
                    </div>
                  </div>) },
              { key: "department", label: "Department", className: "text-txt2",
                sortValue: (p) => p.department?.toLowerCase(), render: (p) => p.department ?? "—" },
              { key: "position", label: "Position", className: "text-txt2",
                sortValue: (p) => p.position?.toLowerCase(), render: (p) => p.position ?? "—" },
              { key: "manager", label: "Manager", className: "text-txt2",
                sortValue: (p) => p.manager_id ? byId[p.manager_id]?.full_name?.toLowerCase() : null,
                render: (p) => p.manager_id ? byId[p.manager_id]?.full_name ?? "—" : "—" },
              { key: "access", label: "Access", sortValue: (p) => p.user_id ? 0 : 1,
                render: (p) => <AccessPill p={p} /> },
              { key: "status", label: "Status", sortValue: (p) => p.effective_state,
                render: (p) => <StatusPill p={p} /> },
              { key: "actions", label: "",
                render: (p) => can("people", "delete") ? (
                  <span onClick={(e) => e.stopPropagation()}>
                    <DeletePersonButton id={p.id} name={p.full_name} />
                  </span>) : null },
            ] as Column<Person>[]}
          />
        </>
      )}

      {openId && <PersonDrawer key={openId} id={openId} onClose={() => setOpenId(null)}
        onEdit={(d) => { setOpenId(null); setEditing(d); }} />}
      {adding && <PersonForm onClose={() => setAdding(false)} />}
      {editing && <PersonForm editing={editing} onClose={() => setEditing(null)} />}
      {importing && <ImportModal onClose={() => setImporting(false)} />}
    </>
  );
}
