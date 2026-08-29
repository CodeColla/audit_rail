import { api } from "./api";

/**
 * Shared evidence-upload plumbing (P5-S3).
 *
 * Before this, uploading only existed inside `Evidence.tsx`'s vault modal, so completing a
 * task or answering an audit question could only *pick* something already in the vault. The
 * activity log for 2026-07-31 shows what that costs in practice: with the vault empty, a
 * task was closed out by attaching a three-day-old unrelated PDF.
 *
 * Deliberately a client-side helper rather than a new API: `POST /evidence` is already
 * multipart and the link/complete endpoints already take an `evidence_id`, so "upload and
 * attach" is two existing calls. Adding multipart variants of those endpoints would mean two
 * more routes duplicating upload + link logic, for a failure mode — second call fails — that
 * leaves the file safely in the vault rather than losing it.
 */

/** The vault's artifact categories. Single source of truth: this list was previously
 *  duplicated verbatim in `Evidence.tsx` and `EvidenceDetail.tsx`, and the e2e database
 *  already contains both `report` and `REPORT` as a result. P5-S6 replaces this with
 *  `LookupSelect` against a real vocabulary; until then, at least it is in one place. */
// `readonly string[]`, not `as const`: EvidenceDetail checks `TYPES.includes(row.type)` for
// a value that came from the database, and a literal-union tuple rejects a plain string.
export const EVIDENCE_TYPES: readonly string[] = [
  "certificate", "report", "policy_doc", "register", "screenshot", "insurance", "other",
];

/** Mirrors `api/config.py`'s `max_upload_mb` default. This is a pre-check to avoid a wasted
 *  round trip, NOT the enforcement boundary — the server's 413 is still the real limit. */
export const MAX_UPLOAD_MB = 25;

/** Strip only the extension, matching the server's `Path(...).stem` default. */
export const stem = (name: string) => name.replace(/\.[^./\\]+$/, "") || name;

export class UploadTooLarge extends Error {
  constructor() { super(`That file is over the ${MAX_UPLOAD_MB} MB limit.`); }
}

/**
 * Upload one file into the evidence vault and return its new id, ready to hand to whichever
 * endpoint wants to link it.
 *
 * Throws `UploadTooLarge` before touching the network for an oversize file — the server
 * would 413 anyway, but only after reading the whole thing into memory first.
 */
export async function uploadEvidence(file: File, opts: {
  title?: string;
  evidenceType?: string;
  issuedAt?: string;
  validUntil?: string;
  /** issue #13: set when this upload is produced by completing a task run, so the general
   * vault list (list_evidence) can hide it by default instead of mixing task-completion
   * artifacts in with deliberately-curated evidence. */
  sourceTaskRunId?: string;
} = {}): Promise<{ id: string }> {
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) throw new UploadTooLarge();

  const fd = new FormData();
  const title = (opts.title ?? "").trim() || stem(file.name);
  fd.append("title", title);
  // `evidence_type` is Form(...) — required — server-side, so it always has to be sent.
  fd.append("evidence_type", opts.evidenceType || "other");
  if (opts.issuedAt) fd.append("issued_at", opts.issuedAt);
  if (opts.validUntil) fd.append("valid_until", opts.validUntil);
  if (opts.sourceTaskRunId) fd.append("source_task_run_id", opts.sourceTaskRunId);
  fd.append("file", file);

  const r = await api.post("/evidence", fd);
  return { id: r.data.id };
}
