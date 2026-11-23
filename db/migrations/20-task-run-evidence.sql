-- #20 S4 — task_run_evidence (backfill links). Hand-applied; there is no migration tool.
--
-- Safe to run more than once: every statement is guarded. Apply to each live database
-- that needs backfills, BEFORE the build that uses it starts. main.py refuses to start
-- against a database that lacks this table.
--
-- Mirrors the table in db/schema.sql. If you change one, change the other.

CREATE TABLE IF NOT EXISTS task_run_evidence (
    task_run_id text NOT NULL,
    evidence_id text NOT NULL,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    created_at  iso_ts NOT NULL DEFAULT now_iso(),
    PRIMARY KEY (task_run_id, evidence_id),
    FOREIGN KEY (task_run_id, tenant_id) REFERENCES task_runs (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_run_evidence_tenant ON task_run_evidence (tenant_id);
