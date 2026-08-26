import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Eye, Plus } from "lucide-react";
import { api, errText, get } from "../../lib/api";
import { useCan } from "../../lib/auth";
import { cn, inputCls, Loading, Modal, PageHead, Pill } from "../../lib/ui";
import { DataTable, Column } from "../../components/DataTable";
import { DocBody } from "../../components/DocBody";

type Doc = {
  id: string; title: string; document_type: string; classification: string; status: string;
  owner_name: string; published_version: string | null; latest_version: string | null;
  latest_status: string | null; next_review_at: string | null; review_status: string;
};
type Person = { id: string; full_name: string };
type PreviewVersion = { id: string; content: string; content_format: "MARKDOWN" | "HTML" | "SHEET" };
type PreviewDetail = {
  versions: PreviewVersion[]; open_version: PreviewVersion | null;
  current_published_version_id: string | null;
};

type DocType = { value: string; label: string };

/**
 * The type list comes from the API (`GET /documents/types`), which reads it from the same
 * constant the router validates against. It used to be hardcoded here and had drifted:
 * it offered "STANDARD", which the database CHECK rejects — picking it produced a 500 —
 * and omitted REGISTER, TEMPLATE and SOA, which are valid.
 */
function useDocTypes() {
  return useQuery({ queryKey: ["document-types"], staleTime: Infinity,
    queryFn: () => get<DocType[]>("/documents/types") });
}

/**
 * P7-S5: classification used to be `CLASSES`, a hardcoded 4-value array — the CHECK
 * constraint `documents.classification` enforced meant that was safe to hardcode. Now that
 * an admin can add or retire values from Admin · Masters (`document_classification` kind,
 * api/domain/vocabularies.py), a hardcoded list here would silently drift from what's
 * actually offered — the exact bug `useDocTypes()` above already exists to avoid for
 * document type, one level up.
 */
function useClassifications() {
  return useQuery({
    queryKey: ["lookups", "document_classification"],
    queryFn: () => get<{ kinds: Record<string, { values: { id: string; value: string }[] }> }>(
      "/lookups?kind=document_classification"),
    select: (r) => r.kinds.document_classification?.values ?? [],
  });
}

/** A document's live state: the newest version's status, shown as a pill. */
function StatusCell({ d }: { d: Doc }) {
  const s = d.latest_status;
  return <span className="flex items-center gap-2">
    {d.status === "ARCHIVED" && <Pill tone="na">Archived</Pill>}
    {s && (
      <Pill tone={s === "PUBLISHED" ? "ok" : s === "PENDING_APPROVAL" ? "warn" : "na"}>
        {s === "PENDING_APPROVAL" ? "In approval" : s.charAt(0) + s.slice(1).toLowerCase()}
      </Pill>
    )}
    {!s && d.status !== "ARCHIVED" && <span className="text-txt3">—</span>}
    {d.latest_version && <span className="font-mono text-caption text-txt3">v{d.latest_version}</span>}
  </span>;
}

function NewDocModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/people") });
  const types = useDocTypes();
  const classes = useClassifications();
  const [f, setF] = useState({ title: "", document_type: "POLICY", classification: "INTERNAL",
    owner_person_id: "", review_cadence_months: "12" });
  // Separate from `f` since it maps to `content_format`, not a field the form otherwise has —
  // spreading it into the POST body directly (see `create` below) would send the wrong key.
  const [kind, setKind] = useState<"DOCUMENT" | "SHEET">("DOCUMENT");
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const create = useMutation({
    mutationFn: () => api.post("/documents", {
      ...f, review_cadence_months: f.review_cadence_months ? +f.review_cadence_months : null,
      content_format: kind === "SHEET" ? "SHEET" : "MARKDOWN" }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["documents"] }); nav(`/documents/${r.data.id}`); },
    onError: (e: any) => setErr(errText(e, "Could not create.")),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (f.title && f.owner_person_id) create.mutate(); };

  return (
    <Modal open onClose={onClose} title="New document">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="text-sm font-medium">Title *
          <input required value={f.title} onChange={set("title")} className={inputCls + " mt-1"}
            placeholder="Information Security Policy" />
        </label>
        <div className="text-sm font-medium">Format
          <div className="mt-1 flex gap-2">
            {([["DOCUMENT", "Document"], ["SHEET", "Spreadsheet"]] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setKind(k)}
                className={cn("rounded-full border px-3 py-1.5 text-label font-medium",
                  kind === k ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                    : "border-bd bg-paper text-txt2 hover:bg-canvas")}>{label}</button>
            ))}
          </div>
          <p className="mt-1 text-caption font-normal text-txt3">
            {kind === "SHEET" ? "A spreadsheet — formulas, formatting and multiple sheets. Published versions freeze their calculated values."
              : "Free-form text, written in the rich text editor."}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm font-medium">Type
            {/* The select renders with zero <option>s while this query is in flight —
                cosmetically blank for a moment, though the underlying default ("POLICY")
                is always a valid type, so it never lets a bad value through on submit. */}
            <select value={f.document_type} onChange={set("document_type")}
              disabled={types.isLoading} className={inputCls + " mt-1 disabled:opacity-50"}>
              {types.isLoading && <option>Loading…</option>}
              {(types.data ?? []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium">Classification
            {/* Same "cosmetically blank while loading, never lets a bad value through"
                behaviour as the Type select above — f.classification defaults to
                "INTERNAL", one of the values this vocabulary is always seeded with. */}
            <select value={f.classification} onChange={set("classification")}
              disabled={classes.isLoading} className={inputCls + " mt-1 capitalize disabled:opacity-50"}>
              {classes.isLoading && <option>Loading…</option>}
              {(classes.data ?? []).map((c) => (
                <option key={c.id} value={c.value}>
                  {c.value.charAt(0) + c.value.slice(1).toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm font-medium">Owner *
            <select required value={f.owner_person_id} onChange={set("owner_person_id")} className={inputCls + " mt-1"}>
              <option value="">— pick a person —</option>
              {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
            </select>
          </label>
          <label className="text-sm font-medium">Review every (months)
            <input type="number" value={f.review_cadence_months} onChange={set("review_cadence_months")} className={inputCls + " mt-1"} />
          </label>
        </div>
        {err && <div className="rounded-md bg-bad-bg px-3 py-2 text-label text-bad">{err}</div>}
        <p className="rounded-md bg-canvas px-3 py-2 text-caption text-txt2">
          Creates a <b>v1.0 draft</b> to write into. It won't publish until it's approved.
        </p>
        <button disabled={!f.title || !f.owner_person_id || create.isPending}
          className="btn btn-primary justify-center disabled:opacity-50">
          {create.isPending ? "Creating…" : "Create & write"}
        </button>
      </form>
    </Modal>
  );
}

/** Quick look at a document's current content without leaving the list — the title link
 * still navigates to the full detail page, same split as Evidence's QuickPreview/title
 * pattern. Shares the `["document", id]` query key with DocumentDetail.tsx, so opening the
 * full page right after a preview is instant. */
function DocPreview({ id, title, onClose }: { id: string; title: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["document", id],
    queryFn: () => get<PreviewDetail>(`/documents/${id}`),
  });
  const shown = useMemo(() => {
    if (!data) return null;
    const pub = data.versions.find((v) => v.id === data.current_published_version_id);
    return data.open_version ?? pub ?? data.versions[0] ?? null;
  }, [data]);
  return (
    <Modal open onClose={onClose} title={title} size="lg">
      {isLoading ? <div className="text-sm text-txt3">Loading…</div>
        : !shown ? <div className="text-sm text-txt3">No content yet.</div>
        : <DocBody content={shown.content} format={shown.content_format}
            className="max-h-[65vh] overflow-y-auto" />}
    </Modal>
  );
}

export default function Documents() {
  const can = useCan();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const typeParam = params.get("type");
  const showArchived = params.get("archived") === "1";
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [previewing, setPreviewing] = useState<{ id: string; title: string } | null>(null);
  const nav = useNavigate();
  const types = useDocTypes();
  const { data, isLoading } = useQuery({
    // `q` is part of the key — six sibling pages share this pattern (Evidence.tsx) so a
    // filtered result written under a bare ["documents", type, archived] key wouldn't
    // silently overwrite the unfiltered one for anyone else navigating away and back.
    queryKey: ["documents", typeParam, showArchived, q],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (typeParam) qs.set("document_type", typeParam);
      if (showArchived) qs.set("include_archived", "true");
      if (q) qs.set("q", q);
      const suffix = qs.toString();
      return get<Doc[]>(`/documents${suffix ? `?${suffix}` : ""}`);
    },
    placeholderData: keepPreviousData,
  });
  const canArchive = can("documents", "edit");
  if (isLoading) return <Loading />;
  const rows = data ?? [];
  const isFiltered = !!typeParam || showArchived || !!q;

  const columns: Column<Doc>[] = [
    {
      key: "title", label: "Title", sortValue: (d) => d.title.toLowerCase(),
      render: (d) => (
        <div className="flex items-center gap-1.5">
          <button onClick={(e) => { e.stopPropagation(); setPreviewing({ id: d.id, title: d.title }); }}
            title={`Preview ${d.title}`}
            className="grid h-6 w-6 shrink-0 place-items-center rounded border border-bd text-txt3 hover:border-accent hover:text-accent">
            <Eye size={13} />
          </button>
          <div>
            <div className="font-medium">{d.title}</div>
            {d.published_version && <div className="font-mono text-caption text-txt3">published v{d.published_version}</div>}
          </div>
        </div>
      ),
    },
    {
      key: "type", label: "Type", sortValue: (d) => d.document_type,
      render: (d) => <span className="capitalize text-txt2">{d.document_type.toLowerCase()}</span>,
    },
    {
      key: "classification", label: "Classification", sortValue: (d) => d.classification,
      render: (d) => <span className="rounded border border-bd bg-canvas px-2 py-0.5 text-caption capitalize text-txt2">{d.classification.toLowerCase()}</span>,
    },
    {
      key: "status", label: "Status", sortValue: (d) => d.latest_status ?? "",
      render: (d) => <StatusCell d={d} />,
    },
    {
      key: "owner", label: "Owner", sortValue: (d) => d.owner_name.toLowerCase(),
      render: (d) => <span className="text-txt2">{d.owner_name}</span>,
    },
    {
      key: "review", label: "Next review", sortValue: (d) => d.next_review_at,
      render: (d) => d.next_review_at
        ? <Pill tone={d.review_status === "overdue" ? "bad" : d.review_status === "due_soon" ? "warn" : "ok"}>
            {d.next_review_at.slice(0, 10)}</Pill>
        : <span className="text-txt3">—</span>,
    },
  ];

  return (
    <>
      <PageHead eyebrow="Program · Documents" title="Documents"
        lead="Policies, procedures and plans — authored, approved by a quorum, published as controlled PDFs. (Registers will render themselves here too, from Sprint 5.)"
        action={can("documents", "add")
          ? <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={15} strokeWidth={2.4} /> New document</button>
          : undefined} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {[{ value: null, label: "All" }, ...(types.data ?? [])].map((c) => (
          <button key={c.label}
            onClick={() => setParams((p) => {
              const next = new URLSearchParams(p);
              if (c.value) next.set("type", c.value); else next.delete("type");
              return next;
            })}
            className={cn("rounded-full border px-3 py-1.5 text-label font-medium",
              (typeParam ?? null) === c.value ? "border-accent bg-[rgba(249,115,22,0.09)] text-ink"
                : "border-bd bg-paper text-txt2 hover:bg-canvas")}>
            {c.label}
          </button>
        ))}
        {/* Archiving added a real state a document could be in with no way to see it: the
            list always excluded ARCHIVED rows and nothing in the SPA ever sent
            include_archived, so an archived document — and its Restore button — became
            unreachable the moment it was archived. */}
        <label className="ml-2 flex items-center gap-1.5 text-label text-txt2">
          <input type="checkbox" checked={showArchived}
            onChange={(e) => setParams((p) => {
              const next = new URLSearchParams(p);
              if (e.target.checked) next.set("archived", "1"); else next.delete("archived");
              return next;
            })} />
          Show archived
        </label>
      </div>

      {rows.length === 0 && !isFiltered ? (
        <div className="rounded-xl border border-dashed border-bd bg-paper p-10 text-center">
          <h3 className="text-body font-semibold">No documents here yet</h3>
          <p className="mx-auto mt-2 max-w-[52ch] text-sm text-txt2">
            This is where "Please share the policy" gets answered. Write your Information Security
            Policy, route it to approvers, and publish a controlled v1.0 — no more Word file on a
            share drive.
          </p>
          {can("documents", "add") && (
            <button onClick={() => setAdding(true)} className="btn btn-primary mt-4"><Plus size={15} strokeWidth={2.4} /> New document</button>
          )}
        </div>
      ) : (
        <DataTable
          rows={rows} getId={(d) => d.id} columns={columns}
          onSearch={setQ} searchPlaceholder="Search titles…"
          onRowClick={(d) => nav(`/documents/${d.id}`)}
          canDelete={canArchive}
          onDeleteOne={(id) => api.patch(`/documents/${id}`, { status: "ARCHIVED" }).then(() => {
            qc.invalidateQueries({ queryKey: ["documents"] });
          })}
          bulkActionCopy={{
            button: "Archive selected", busy: "Archiving…",
            confirm: (label) => `Archive ${label}? You can restore them later from "Show archived".`,
            errorPrefix: "could not be archived",
          }}
          emptyMessage="No documents match these filters."
          noMatchMessage={`No documents match "${q}".`}
        />
      )}
      {adding && <NewDocModal onClose={() => setAdding(false)} />}
      {previewing && <DocPreview id={previewing.id} title={previewing.title} onClose={() => setPreviewing(null)} />}
      <p className="mt-6 text-caption text-txt3">
        Looking for the old Policies page? It's here now — <Link to="/documents?type=POLICY" className="underline">Documents → Policies</Link>.
      </p>
    </>
  );
}
