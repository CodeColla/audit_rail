-- audit_rail — multi-tenant compliance portal schema (Phase 1 · ported to PostgreSQL)
-- Target: PostgreSQL 16 (docker-compose.yml — host port 5433, container 5432).
-- Conventions (kept deliberately portable; v2 will go PG-native — see docs/phase3):
--   * TEXT UUIDs for ids (app-generated), ISO-8601 UTC TEXT timestamps (app-set)
--   * enums = TEXT + CHECK; booleans = INTEGER 0/1; JSON = TEXT
--   * FKs are enforced by PostgreSQL natively (no PRAGMA needed)
-- Applied by scripts/init_db.py, which resets the `public` schema first.
-- Every tenant-owned table carries tenant_id. Auditor guests are additionally
-- scoped per assessment (assessment_guests) — enforced in the API layer.

-- ============================================================ identity & tenancy

CREATE TABLE tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at  TEXT NOT NULL
);

CREATE TABLE users (
    id                 TEXT PRIMARY KEY,
    email              TEXT NOT NULL UNIQUE,
    full_name          TEXT NOT NULL,
    password_hash      TEXT,                 -- NULL until first login is set / external auth
    auth_provider      TEXT NOT NULL DEFAULT 'local' CHECK (auth_provider IN ('local', 'sr_iam')),
    is_platform_admin  INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invited', 'disabled')),
    created_at         TEXT NOT NULL,
    last_login_at      TEXT
);

CREATE TABLE tenant_members (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    role        TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'member')),
    created_at  TEXT NOT NULL,
    UNIQUE (tenant_id, user_id)
);

-- ============================================================ files (shared vault)

CREATE TABLE files (
    id                     TEXT PRIMARY KEY,
    tenant_id              TEXT NOT NULL REFERENCES tenants(id),
    storage_key            TEXT NOT NULL UNIQUE,   -- local path key now, S3 key later (D4)
    original_name          TEXT NOT NULL,
    mime_type              TEXT,
    size_bytes             INTEGER,
    sha256                 TEXT,
    uploaded_by_member_id  TEXT REFERENCES tenant_members(id),
    created_at             TEXT NOT NULL
);

-- ============================================================ control library (menu: Controls)

CREATE TABLE domains (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    parent_id   TEXT REFERENCES domains(id),     -- NULL = top-level; one nesting level used
    code        TEXT,                            -- short domain code, e.g. AM, NI (design handoff)
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE controls (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    domain_id            TEXT NOT NULL REFERENCES domains(id),
    code                 TEXT NOT NULL,          -- e.g. GOV-01
    statement            TEXT NOT NULL,          -- canonical control wording
    guidance             TEXT,
    lifecycle            TEXT NOT NULL CHECK (lifecycle IN ('one_time', 'recurring', 'per_audit')),
    recurrence_months    INTEGER,                -- set when lifecycle = 'recurring'
    applicability        TEXT NOT NULL DEFAULT 'applicable'
                             CHECK (applicability IN ('applicable', 'not_applicable')),
    na_justification     TEXT,                   -- required by app when not_applicable
    reactivation_trigger TEXT,                   -- e.g. 'cloud adoption', 'AI tool in scope'
    stock_response       TEXT CHECK (stock_response IN ('yes', 'partial', 'no', 'na')),
    stock_comment        TEXT,                   -- the reusable answer text
    owner_member_id      TEXT REFERENCES tenant_members(id),
    framework_refs       TEXT,                   -- JSON tags: ["RBI-ITO 5.2", "ISO27001 A.5.1"]
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (tenant_id, code)
);

CREATE TABLE control_evidence_requirements (
    id            TEXT PRIMARY KEY,
    control_id    TEXT NOT NULL REFERENCES controls(id),
    evidence_type TEXT NOT NULL,                 -- vocabulary: Phase 0 Doc 2 §3
    note          TEXT
);

-- ============================================================ policy register (menu: Policies)

CREATE TABLE policies (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id),
    title                 TEXT NOT NULL,
    description           TEXT,
    owner_member_id       TEXT REFERENCES tenant_members(id),
    review_cadence_months INTEGER,               -- annual review = 12 (AnnexC #7)
    next_review_at        TEXT,
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'retired')),
    created_at            TEXT NOT NULL
);

-- current version = latest effective_from (derived, not stored)
CREATE TABLE policy_versions (
    id                    TEXT PRIMARY KEY,
    policy_id             TEXT NOT NULL REFERENCES policies(id),
    version_label         TEXT NOT NULL,
    file_id               TEXT REFERENCES files(id),
    approved_by_member_id TEXT REFERENCES tenant_members(id),
    approved_at           TEXT,
    effective_from        TEXT,
    notes                 TEXT,
    created_at            TEXT NOT NULL
);

-- ============================================================ evidence vault (menu: Evidence)

CREATE TABLE evidence (
    id                   TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL REFERENCES tenants(id),
    title                TEXT NOT NULL,
    evidence_type        TEXT NOT NULL,          -- policy_doc | certificate | report | log | screenshot | ...
    file_id              TEXT REFERENCES files(id),
    external_url         TEXT,                   -- alternative to a stored file
    policy_version_id    TEXT REFERENCES policy_versions(id),  -- when the artifact IS a policy
    issued_at            TEXT,
    valid_until          TEXT,                   -- freshness: a 2023 VAPT ≠ 2026 evidence
    notes                TEXT,
    created_by_member_id TEXT REFERENCES tenant_members(id),
    created_at           TEXT NOT NULL
);

CREATE TABLE evidence_controls (
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    control_id  TEXT NOT NULL REFERENCES controls(id),
    PRIMARY KEY (evidence_id, control_id)
);

-- ============================================== checklist templates (menu: Audits › Templates)

CREATE TABLE templates (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(id),
    bank_name      TEXT NOT NULL,
    title          TEXT NOT NULL,
    version_label  TEXT,                         -- banks version checklists (AnnexC v2.7)
    source_file_id TEXT REFERENCES files(id),    -- original xlsx, kept for export (D6)
    status         TEXT NOT NULL DEFAULT 'importing'
                       CHECK (status IN ('importing', 'active', 'archived')),
    notes          TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE template_sections (
    id          TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES templates(id),
    parent_id   TEXT REFERENCES template_sections(id),  -- KSL: section > sub-section
    title       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE questions (
    id                 TEXT PRIMARY KEY,
    template_id        TEXT NOT NULL REFERENCES templates(id),
    section_id         TEXT REFERENCES template_sections(id),
    number             TEXT,                     -- bank's numbering, kept verbatim (dupes exist: KSL #89)
    text               TEXT NOT NULL,
    rationale          TEXT,                     -- AnnexC "Rationale for the domain"
    testing_procedure  TEXT,                     -- AnnexC col I
    evidence_mandatory INTEGER NOT NULL DEFAULT 0,
    classification     TEXT,                     -- AnnexC "Entity Level"
    response_scale     TEXT,                     -- JSON, e.g. ["yes","no","na"]
    sort_order         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE question_control_map (
    id                     TEXT PRIMARY KEY,
    question_id            TEXT NOT NULL REFERENCES questions(id),
    control_id             TEXT NOT NULL REFERENCES controls(id),
    confidence             REAL,                 -- similarity score from the mapper (D5)
    status                 TEXT NOT NULL DEFAULT 'suggested'
                               CHECK (status IN ('suggested', 'confirmed', 'rejected')),
    confirmed_by_member_id TEXT REFERENCES tenant_members(id),
    created_at             TEXT NOT NULL,
    UNIQUE (question_id, control_id)
);

CREATE TABLE scoring_configs (
    id          TEXT PRIMARY KEY,
    template_id TEXT NOT NULL UNIQUE REFERENCES templates(id),
    config      TEXT NOT NULL,   -- JSON: response scale, LxI matrix, status vocab, verdict bands (D7)
    updated_at  TEXT NOT NULL
);

-- ============================================================ assessments (menu: Audits)

CREATE TABLE assessments (
    id                    TEXT PRIMARY KEY,
    tenant_id             TEXT NOT NULL REFERENCES tenants(id),
    template_id           TEXT NOT NULL REFERENCES templates(id),
    title                 TEXT NOT NULL,
    bank_name             TEXT,
    period_start          TEXT,
    period_end            TEXT,
    status                TEXT NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'in_progress', 'submitted',
                                                'in_review', 'verdict_issued', 'closed')),
    predicted_verdict     TEXT,                  -- computed from scoring_configs (D7)
    verdict               TEXT,                  -- bank's actual verdict
    verdict_notes         TEXT,
    vendor_spoc_member_id TEXT REFERENCES tenant_members(id),
    bank_spoc_name        TEXT,
    bank_spoc_email       TEXT,
    created_by_member_id  TEXT REFERENCES tenant_members(id),
    created_at            TEXT NOT NULL,
    closed_at             TEXT
);

-- auditor guest access: per-assessment, expiring, revocable (D2)
CREATE TABLE assessment_guests (
    id                   TEXT PRIMARY KEY,
    assessment_id        TEXT NOT NULL REFERENCES assessments(id),
    user_id              TEXT NOT NULL REFERENCES users(id),
    role                 TEXT NOT NULL DEFAULT 'auditor' CHECK (role IN ('auditor', 'observer')),
    invited_by_member_id TEXT REFERENCES tenant_members(id),
    invited_at           TEXT NOT NULL,
    expires_at           TEXT,
    revoked_at           TEXT,
    UNIQUE (assessment_id, user_id)
);

CREATE TABLE responses (
    id                        TEXT PRIMARY KEY,
    assessment_id             TEXT NOT NULL REFERENCES assessments(id),
    question_id               TEXT NOT NULL REFERENCES questions(id),
    response_value            TEXT CHECK (response_value IN ('yes', 'partial', 'no', 'na')),
    comment                   TEXT,
    na_justification          TEXT,              -- app blocks validation of 'na' without it
    workflow_status           TEXT NOT NULL DEFAULT 'open'
                                  CHECK (workflow_status IN ('open', 'answered', 'ask_pending',
                                                             'actioned', 'validated', 'final')),
    final_status              TEXT CHECK (final_status IN ('compliant', 'partially_compliant',
                                                           'non_compliant', 'not_applicable',
                                                           'clarification')),
    prefilled_from_control_id TEXT REFERENCES controls(id),   -- provenance of the stock answer
    updated_by_user_id        TEXT REFERENCES users(id),
    updated_at                TEXT NOT NULL,
    UNIQUE (assessment_id, question_id)
);

CREATE TABLE response_revisions (
    id             TEXT PRIMARY KEY,
    response_id    TEXT NOT NULL REFERENCES responses(id),
    rev_no         INTEGER NOT NULL,
    response_value TEXT,
    comment        TEXT,
    author_user_id TEXT REFERENCES users(id),
    created_at     TEXT NOT NULL,
    UNIQUE (response_id, rev_no)
);

CREATE TABLE response_evidence (
    response_id TEXT NOT NULL REFERENCES responses(id),
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (response_id, evidence_id)
);

-- per-question conversation: auditor remarks + the KSL ask→action→validation rounds
CREATE TABLE review_messages (
    id             TEXT PRIMARY KEY,
    assessment_id  TEXT NOT NULL REFERENCES assessments(id),
    response_id    TEXT REFERENCES responses(id),   -- NULL = assessment-level remark
    author_user_id TEXT NOT NULL REFERENCES users(id),
    author_kind    TEXT NOT NULL CHECK (author_kind IN ('member', 'auditor', 'system')),
    kind           TEXT NOT NULL CHECK (kind IN ('remark', 'ask', 'action', 'validation')),
    body           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    resolved_at    TEXT
);

CREATE TABLE findings (
    id                 TEXT PRIMARY KEY,
    assessment_id      TEXT NOT NULL REFERENCES assessments(id),
    response_id        TEXT REFERENCES responses(id),
    raised_by_user_id  TEXT NOT NULL REFERENCES users(id),   -- member or auditor guest
    title              TEXT NOT NULL,
    description        TEXT,
    recommendation     TEXT,
    likelihood         INTEGER CHECK (likelihood BETWEEN 1 AND 3),   -- AnnexC 3x3 model
    impact             INTEGER CHECK (impact BETWEEN 1 AND 3),
    risk_score         INTEGER,                  -- likelihood * impact, app-computed
    risk_rating        TEXT CHECK (risk_rating IN ('low', 'medium', 'high', 'clarification')),
    status             TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open', 'remediation', 'closed', 'accepted')),
    owner_member_id    TEXT REFERENCES tenant_members(id),
    due_at             TEXT,
    closed_at          TEXT,
    created_at         TEXT NOT NULL
);

-- ============================================================ tasks & calendar (menu: Tasks)

CREATE TABLE tasks (
    id                 TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants(id),
    control_id         TEXT REFERENCES controls(id),   -- source of recurring obligation
    policy_id          TEXT REFERENCES policies(id),   -- or a policy review cycle
    title              TEXT NOT NULL,
    description        TEXT,
    cadence_months     INTEGER,                 -- NULL = one-off
    assignee_member_id TEXT REFERENCES tenant_members(id),
    next_due_at        TEXT,
    status             TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'paused', 'completed')),
    created_at         TEXT NOT NULL
);

CREATE TABLE task_runs (
    id                     TEXT PRIMARY KEY,
    task_id                TEXT NOT NULL REFERENCES tasks(id),
    due_at                 TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'done', 'overdue', 'skipped')),
    completed_at           TEXT,
    completed_by_member_id TEXT REFERENCES tenant_members(id),
    evidence_id            TEXT REFERENCES evidence(id),  -- the dated artifact this run produced
    notes                  TEXT
);

-- ============================================================ platform (menus: Dashboard/Admin)

CREATE TABLE notifications (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    type        TEXT NOT NULL,                   -- task_due | evidence_expiring | new_ask | ...
    title       TEXT NOT NULL,
    body        TEXT,
    entity_type TEXT,
    entity_id   TEXT,
    read_at     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE activity_log (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    actor_user_id TEXT REFERENCES users(id),
    action        TEXT NOT NULL,                 -- response.updated | evidence.uploaded | ...
    entity_type   TEXT,
    entity_id     TEXT,
    detail        TEXT,                          -- JSON diff/context
    created_at    TEXT NOT NULL
);

-- ============================================================ indexes

CREATE INDEX idx_members_tenant       ON tenant_members(tenant_id);
CREATE INDEX idx_members_user         ON tenant_members(user_id);
CREATE INDEX idx_files_tenant         ON files(tenant_id);
CREATE INDEX idx_domains_tenant       ON domains(tenant_id);
CREATE INDEX idx_controls_tenant      ON controls(tenant_id);
CREATE INDEX idx_controls_domain      ON controls(domain_id);
CREATE INDEX idx_policies_tenant      ON policies(tenant_id);
CREATE INDEX idx_policies_review      ON policies(next_review_at);
CREATE INDEX idx_polversions_policy   ON policy_versions(policy_id);
CREATE INDEX idx_evidence_tenant      ON evidence(tenant_id);
CREATE INDEX idx_evidence_expiry      ON evidence(valid_until);
CREATE INDEX idx_templates_tenant     ON templates(tenant_id);
CREATE INDEX idx_sections_template    ON template_sections(template_id);
CREATE INDEX idx_questions_template   ON questions(template_id);
CREATE INDEX idx_qcm_question         ON question_control_map(question_id);
CREATE INDEX idx_qcm_control          ON question_control_map(control_id);
CREATE INDEX idx_assessments_tenant   ON assessments(tenant_id);
CREATE INDEX idx_guests_assessment    ON assessment_guests(assessment_id);
CREATE INDEX idx_guests_user          ON assessment_guests(user_id);
CREATE INDEX idx_responses_assessment ON responses(assessment_id);
CREATE INDEX idx_revisions_response   ON response_revisions(response_id);
CREATE INDEX idx_messages_response    ON review_messages(response_id);
CREATE INDEX idx_messages_assessment  ON review_messages(assessment_id);
CREATE INDEX idx_findings_assessment  ON findings(assessment_id);
CREATE INDEX idx_tasks_tenant         ON tasks(tenant_id);
CREATE INDEX idx_tasks_due            ON tasks(next_due_at);
CREATE INDEX idx_taskruns_task        ON task_runs(task_id);
CREATE INDEX idx_taskruns_due         ON task_runs(due_at, status);
CREATE INDEX idx_notifications_user   ON notifications(user_id, read_at);
CREATE INDEX idx_activity_tenant      ON activity_log(tenant_id, created_at);
