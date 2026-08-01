import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, errText, get } from "../lib/api";
import { useAuth, useCan } from "../lib/auth";
import { Card, cn, inputCls, Loading, PageHead, Pill } from "../lib/ui";

/**
 * Masters — the editable vocabularies behind every dropdown in the app (P5-S6).
 *
 * This screen is the direct answer to Sumit's report that a Department "can't be extended".
 * The backend has existed since P4-S3 (`/lookups`, full CRUD, gated `org.edit`) with **zero
 * frontend callers** — the entire gap was that nothing rendered it. So this is almost purely
 * UI over an API that was already finished and already tested.
 *
 * Values are free text in their own tables. A vocabulary is an *affordance*, not a
 * constraint: hiding "Finance" stops it being offered without rewriting the people already
 * sitting in it. That is why Hide is the prominent action and Delete is secondary.
 */

type Value = { id: string; value: string; sort_order: number; is_active: number };
type Kinds = Record<string, { label: string; values: Value[] }>;

function KindCard({ kind, label, values, canEdit }: {
  kind: string; label: string; values: Value[]; canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState("");

  // Invalidate BOTH the admin list and the per-kind key `LookupSelect` reads. Without the
  // second one, a Department added here would not appear in the People form until a reload —
  // which is the exact journey this sprint exists to fix.
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["lookups-admin"] });
    qc.invalidateQueries({ queryKey: ["lookups", kind] });
  };
  const fail = (fallback: string) => (e: any) => setErr(errText(e, fallback));

  const add = useMutation({
    mutationFn: (value: string) => api.post("/lookups", { kind, value }),
    onSuccess: () => { setErr(""); setAdding(""); refresh(); },
    onError: fail("Could not add that value."),
  });
  const patch = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) =>
      api.patch(`/lookups/${v.id}`, v.body),
    onSuccess: () => { setErr(""); setRenaming(null); refresh(); },
    onError: fail("Could not save that change."),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/lookups/${id}`),
    onSuccess: () => { setErr(""); refresh(); },
    onError: fail("Could not remove that value."),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (adding.trim()) add.mutate(adding.trim());
  };

  return (
    <Card>
      <div className="mb-2 flex items-baseline justify-between">
        <div className="eyebrow">{label}</div>
        <span className="text-[11px] text-txt3 tnum">
          {values.filter((v) => v.is_active).length} in use
        </span>
      </div>

      <div className="divide-y divide-bd rounded-md border border-bd">
        {values.length === 0 && (
          <div className="px-3 py-2 text-[12px] text-txt3">Nothing in this list yet.</div>
        )}
        {values.map((v) => (
          <div key={v.id} className="flex items-center gap-2 px-3 py-1.5">
            {renaming === v.id ? (
              <>
                <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && draft.trim()) {
                      patch.mutate({ id: v.id, body: { value: draft.trim() } });
                    }
                    if (e.key === "Escape") setRenaming(null);
                  }}
                  className={inputCls + " py-1 text-[12.5px]"} />
                <button onClick={() => draft.trim() && patch.mutate({ id: v.id, body: { value: draft.trim() } })}
                  className="btn shrink-0 py-1 text-[12px]">Save</button>
                <button onClick={() => setRenaming(null)}
                  className="shrink-0 text-[12px] text-txt3 hover:underline">Cancel</button>
              </>
            ) : (
              <>
                <span className={cn("min-w-0 flex-1 truncate text-[12.5px]",
                  !v.is_active && "text-txt3 line-through")}>{v.value}</span>
                {!v.is_active && <Pill tone="na">hidden</Pill>}
                {canEdit && (
                  <>
                    <button onClick={() => { setRenaming(v.id); setDraft(v.value); }}
                      className="shrink-0 text-[12px] text-txt3 hover:text-accent">Rename</button>
                    {/* Hide before delete: records already carrying the value keep it, the
                        value just stops being offered. Delete is the sharp edge. */}
                    <button onClick={() => patch.mutate({ id: v.id, body: { is_active: v.is_active ? 0 : 1 } })}
                      className="shrink-0 text-[12px] text-txt3 hover:text-accent">
                      {v.is_active ? "Hide" : "Show"}
                    </button>
                    <button aria-label={`Delete ${v.value}`}
                      onClick={() => { if (confirm(
                          `Remove "${v.value}" from ${label}?\n\nRecords already using it keep ` +
                          `the text — it just stops being offered. "Hide" does the same thing ` +
                          `reversibly.`)) remove.mutate(v.id); }}
                      className="shrink-0 text-txt3 hover:text-bad"><Trash2 size={13} /></button>
                  </>
                )}
              </>
            )}
          </div>
        ))}
      </div>

      {err && (
        <div role="alert" className="mt-2 rounded-md bg-bad-bg px-2.5 py-1.5 text-[11.5px] text-bad">
          {err}
        </div>
      )}

      {canEdit && (
        <form onSubmit={submit} className="mt-2 flex gap-2">
          <input value={adding} onChange={(e) => setAdding(e.target.value)}
            placeholder={`Add to ${label.toLowerCase()}…`}
            className={inputCls + " py-1 text-[12.5px]"} />
          <button disabled={!adding.trim() || add.isPending}
            className="btn btn-primary shrink-0 py-1 text-[12px] disabled:opacity-50">Add</button>
        </form>
      )}
    </Card>
  );
}

export default function Admin() {
  const { user } = useAuth();
  const can = useCan();
  const canEdit = can("org", "edit");
  const { data, isLoading } = useQuery({
    queryKey: ["lookups-admin"],
    // include_inactive: managing values IS the point of this screen, hidden ones included.
    // A form asks for the active set instead.
    queryFn: () => get<{ kinds: Kinds }>("/lookups?include_inactive=true"),
  });

  if (isLoading) return <Loading />;
  const kinds = data?.kinds ?? {};

  return (
    <>
      <PageHead eyebrow="Administration" title="Masters"
        lead="The lists behind every dropdown in the app. Add your own values — departments, categories, job titles — and they appear immediately wherever that field is used." />

      {!canEdit && (
        <div className="mb-4 rounded-md bg-canvas px-3 py-2 text-[12.5px] text-txt2">
          You can see these lists but not change them — editing needs the Organisation permission.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(kinds).map(([kind, k]) => (
          <KindCard key={kind} kind={kind} label={k.label} values={k.values} canEdit={canEdit} />
        ))}
      </div>

      <Card className="mt-4 max-w-lg">
        <div className="eyebrow mb-2">Signed in as</div>
        <div className="text-[15px] font-semibold">{user?.full_name}</div>
        <div className="text-[13px] capitalize text-txt2">{user?.role}</div>
      </Card>
    </>
  );
}
