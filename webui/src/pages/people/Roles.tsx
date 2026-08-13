import { useState } from "react";
import { Plus } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errText, get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { Loading, Modal, PageHead, Pill, TH, Table, Td, cn, inputCls } from "../../lib/ui";

type Vocab = { modules: { key: string; label: string; actions: string[] }[] };
type Role = {
  id: string; name: string; description: string | null; is_system: number;
  permissions: string[]; member_count: number;
};

const ACTION_LABEL: Record<string, string> = {
  view: "View", add: "Add", edit: "Edit", delete: "Delete",
  approve: "Approve", publish: "Publish",
};

/** The checkbox matrix: one row per module, one box per action it supports. */
function Matrix({ vocab, value, onChange, disabled }: {
  vocab: Vocab; value: Set<string>; onChange: (next: Set<string>) => void; disabled?: boolean;
}) {
  const toggle = (key: string) => {
    const next = new Set(value);
    next.has(key) ? next.delete(key) : next.add(key);
    onChange(next);
  };
  const allActions = ["view", "add", "edit", "delete", "approve", "publish"];

  return (
    <div className="overflow-x-auto rounded-md border border-bd">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className={cn(TH, "px-3 py-2")}>
              Module
            </th>
            {allActions.map((a) => (
              <th key={a} className={cn(TH, "w-20 px-2 py-2 text-center")}>
                {ACTION_LABEL[a]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {vocab.modules.map((m) => (
            <tr key={m.key} className="border-b border-bd last:border-0">
              <td className="px-3 py-1.5 font-medium">{m.label}</td>
              {allActions.map((a) => {
                const supported = m.actions.includes(a);
                const key = `${m.key}.${a}`;
                return (
                  <td key={a} className="px-2 py-1.5 text-center">
                    {supported ? (
                      <input type="checkbox" aria-label={`${m.label} ${ACTION_LABEL[a]}`}
                        disabled={disabled} checked={value.has(key)}
                        onChange={() => toggle(key)} />
                    ) : (
                      <span className="text-txt3">·</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RoleModal({ role, vocab, onClose }: {
  role: Role | null; vocab: Vocab; onClose: () => void;
}) {
  const qc = useQueryClient();
  const readOnly = !!role?.is_system;
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [perms, setPerms] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const [err, setErr] = useState("");

  const save = useMutation({
    mutationFn: () => {
      const body = { name, description: description || null, permissions: [...perms] };
      return role ? api.patch(`/roles/${role.id}`, body) : api.post("/roles", body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["roles"] }); onClose(); },
    onError: (e: any) => setErr(errText(e, "Could not save the role.")),
  });

  return (
    <Modal open onClose={onClose} size="xl" title={role ? role.name : "New role"}>
      <div className="flex flex-col gap-3">
        {readOnly && (
          <div className="rounded-md bg-canvas px-3 py-2 text-label text-txt2">
            This is a built-in role, so it can't be edited — that keeps an admin from
            removing their own access. Create a new role to customise permissions.
          </div>
        )}
        <div>
          <label htmlFor="role-name" className="text-sm font-medium">Name</label>
          <input id="role-name" value={name} disabled={readOnly}
            onChange={(e) => setName(e.target.value)} className={inputCls + " mt-1"}
            placeholder="Compliance Analyst" />
        </div>
        <div>
          <label htmlFor="role-desc" className="text-sm font-medium">Description</label>
          <input id="role-desc" value={description} disabled={readOnly}
            onChange={(e) => setDescription(e.target.value)} className={inputCls + " mt-1"} />
        </div>
        <div>
          <div className="eyebrow mb-1.5">Permissions · {perms.size} selected</div>
          <Matrix vocab={vocab} value={perms} onChange={setPerms} disabled={readOnly} />
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        {!readOnly && (
          <button disabled={!name || save.isPending} onClick={() => save.mutate()}
            className="btn btn-primary justify-center disabled:opacity-50">
            {save.isPending ? "Saving…" : role ? "Save changes" : "Create role"}
          </button>
        )}
      </div>
    </Modal>
  );
}

export default function Roles() {
  const can = useCan();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Role | null | undefined>(undefined);
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<Role[]>("/roles") });
  const vocab = useQuery({ queryKey: ["role-vocab"], queryFn: () => get<Vocab>("/roles/vocabulary") });
  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/roles/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["roles"] }),
    onError: (e: any) => alert(errText(e, "Could not delete the role.")),
  });

  if (roles.isLoading || vocab.isLoading || !vocab.data) return <Loading />;

  return (
    <>
      <PageHead eyebrow="Administration" title="Roles"
        lead="A role is a set of permissions. Everyone with a login holds exactly one — it decides which menus they see and what the API lets them do."
        action={can("roles", "add")
          ? <button onClick={() => setEditing(null)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New role</button>
          : undefined} />

      <Table head={["Role", "Description", "Permissions", "People", ""]}>
        {(roles.data ?? []).map((r) => (
          <tr key={r.id} className="hover:bg-canvas">
            <Td>
              <span className="font-medium">{r.name}</span>
              {!!r.is_system && <Pill tone="na">built-in</Pill>}
            </Td>
            <Td className="text-txt2">{r.description ?? "—"}</Td>
            <Td className="text-txt2 tnum">{r.permissions.length}</Td>
            <Td className="text-txt2 tnum">{r.member_count}</Td>
            <Td>
              <span className="flex justify-end gap-1.5">
                <button onClick={() => setEditing(r)} className="btn py-1 text-label">
                  {r.is_system || !can("roles", "edit") ? "View" : "Edit"}
                </button>
                {!r.is_system && can("roles", "delete") && (
                  <button title="Delete"
                    onClick={() => { if (confirm(`Delete the role "${r.name}"?`)) del.mutate(r.id); }}
                    className="rounded-md border border-bd px-2 py-1 text-label text-txt2 hover:border-bad hover:text-bad">
                    ✕
                  </button>
                )}
              </span>
            </Td>
          </tr>
        ))}
      </Table>

      {editing !== undefined && (
        <RoleModal role={editing} vocab={vocab.data} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
