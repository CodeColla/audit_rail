-- =====================================================================================
-- audit_rail v2 — FINAL PostgreSQL schema  (KIAM INTL PVT LTD)
-- Target: PostgreSQL 16 (docker-compose, host port 5433). Extensions required: NONE.
-- Supersedes db/schema.sql (27 tables). 60 tables. Applied by scripts/init_db.py.
--
-- TYPE POLICY (deliberate, and the single most important departure from both drafts):
--   * ids           = TEXT, DEFAULT gen_random_uuid()::text  — NOT native uuid.
--   * timestamps    = ISO-8601 UTC TEXT (domain iso_ts)      — NOT timestamptz/date.
--   * booleans      = INTEGER 0/1 on ported columns; native boolean on new tables.
--   * JSON          = TEXT on ported columns; jsonb on new columns.
--   WHY: api/database.py does `metadata.reflect(bind=engine)` and the whole API is
--   SQLAlchemy Core over reflected types. The app generates ids as str(uuid.uuid4())
--   and timestamps as util.now_iso() strings, and api/util.py slices them (`iso_date[:10]`).
--   Native uuid/timestamptz would change reflected Python types under 35+ call sites and
--   hard-break util.evidence_status()/review_status() (a `date` object is not sliceable),
--   which the dashboard calls on every request. ISO-8601 UTC TEXT sorts and range-scans
--   lexicographically == chronologically, so every queue index below is exact. Views cast
--   ::date where interval maths is needed. Cost: 36-byte ids. At 2-10 users, irrelevant.
--   Bonus: hashing TEXT timestamps is deterministic; timestamptz->text depends on the
--   DateStyle/TimeZone GUCs and is NOT immutable — the hash chain gets simpler, not harder.
--
-- ENUM POLICY: TEXT + CHECK everywhere (as the ported schema already does). No native
--   ENUM types: they buy nothing here and every ALTER TYPE is friction for a 2-10 team.
--   CASING: ported tables keep their exact lowercase vocab (zero app churn); new tables
--   use the docs' UPPERCASE. Mixed casing is a known, accepted papercut — see MIGRATION.
--
-- TENANCY: every tenant-owned table carries tenant_id, and every FK to a tenant-owned
--   parent is COMPOSITE — (tenant_id, parent_id) -> parent(tenant_id, id). Cross-tenant
--   linkage is structurally impossible, not merely unwritten. Child tables whose inserts
--   don't supply tenant_id get it from their parent via the inherit_tenant() trigger, so
--   NOT NULL + composite FK cost the app ZERO code changes.
-- =====================================================================================

-- Precondition: content hashes are only stable on a UTF8 database.
DO $$ BEGIN
    IF (SELECT pg_encoding_to_char(encoding) FROM pg_database
         WHERE datname = current_database()) <> 'UTF8' THEN
        RAISE EXCEPTION 'audit_rail requires a UTF8 database (content hashes assume it)';
    END IF;
END $$;


-- =====================================================================================
-- DOMAINS, HELPERS, TRIGGER FUNCTIONS
-- =====================================================================================

-- Permissive on purpose: accepts 'YYYY-MM-DD' (util.add_months output) and
-- 'YYYY-MM-DDTHH:MM:SSZ' (util.now_iso output). Both ::date-cast cleanly.
CREATE DOMAIN iso_ts AS text
    CHECK (VALUE ~ '^\d{4}-\d{2}-\d{2}([T ][0-9:.\+\-Z]*)?$');

-- Case-insensitive email without the citext extension.
CREATE DOMAIN email_addr AS text
    CHECK (VALUE = lower(VALUE) AND VALUE LIKE '%_@_%');

CREATE FUNCTION now_iso() RETURNS iso_ts LANGUAGE sql STABLE AS $$
    SELECT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')::iso_ts $$;

CREATE FUNCTION sha256_hex(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(t, 'UTF8')), 'hex') $$;

-- Fills tenant_id from the row's parent when the app didn't supply it.
-- TG_ARGV[0] = parent table, TG_ARGV[1] = fk column on NEW.
-- This is what lets `insert(t("responses")).values(id=..., assessment_id=...)` — which
-- supplies NO tenant_id (assessments.py:196) — keep working against a NOT NULL column.
CREATE FUNCTION inherit_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE fk text;
BEGIN
    IF NEW.tenant_id IS NULL THEN
        fk := to_jsonb(NEW) ->> TG_ARGV[1];
        IF fk IS NOT NULL THEN
            EXECUTE format('SELECT tenant_id FROM %I WHERE id = $1', TG_ARGV[0])
              INTO NEW.tenant_id USING fk;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now_iso(); RETURN NEW; END $$;

-- Immutability. Denies UPDATE only (see M6 in the review notes): a DELETE arriving via
-- ON DELETE CASCADE from a legitimately-deleted parent must not deadlock the cascade.
-- activity_log additionally denies DELETE — its parent FK is RESTRICT, so nothing cascades.
CREATE FUNCTION deny_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME; END $$;

CREATE FUNCTION deny_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only (no UPDATE/DELETE)', TG_TABLE_NAME; END $$;


-- =====================================================================================
-- L0 · IDENTITY, TENANCY, PEOPLE, FILES
-- =====================================================================================

-- The customer org (P4: what the UI calls an "Organisation"). Single DB, app-enforced
-- scoping + composite FKs + (optional) RLS.
-- gst_number is the anti-duplicate key: one GSTIN, one organisation, globally. It is
-- NULLABLE rather than NOT NULL because Postgres allows many NULLs under a UNIQUE index,
-- which keeps pre-P4 tenants and test fixtures valid; POST /auth/signup requires it.
CREATE TABLE tenants (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name                text NOT NULL,
    slug                text NOT NULL UNIQUE,
    gst_number          text UNIQUE,
    super_admin_user_id text,                       -- FK added in CROSS-LAYER section
    logo_file_id        text,                       -- P6. FK added in CROSS-LAYER section
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at          iso_ts NOT NULL DEFAULT now_iso()
);

-- A LOGIN. Auditor guests are users with no people row.
-- P4 NOTE: every PERSON now gets one of these too (created in status='invited' with a NULL
-- password_hash; they set the password themselves via user_invites). people.user_id is the
-- bridge. Magic-link attestation still works without any login — signing.py derives
-- everything from the token — so staff who never sign in are unaffected.
-- email is GLOBALLY unique: one human = one login across every organisation, with several
-- tenant_members rows.
CREATE TABLE users (
    id                 text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email              text NOT NULL UNIQUE,
    full_name          text NOT NULL,
    password_hash      text,
    auth_provider      text NOT NULL DEFAULT 'local' CHECK (auth_provider IN ('local', 'sr_iam')),
    is_platform_admin  integer NOT NULL DEFAULT 0 CHECK (is_platform_admin IN (0, 1)),
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invited', 'disabled')),
    created_at         iso_ts NOT NULL DEFAULT now_iso(),
    last_login_at      iso_ts
);

-- P4. Password policy support: min 8 alphanumeric, expires after 30 days, and the previous
-- 3 hashes may not be reused.
--   level 0 = current password, 1 = previous, 2 = the one before that.
-- A change shifts everyone down and deletes level 3, so at most 3 rows exist per user.
-- Expiry is measured from the level-0 row's changed_at, which is why the timestamp lives
-- here rather than on users.
CREATE TABLE user_password_history (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    password_hash text NOT NULL,
    level       smallint NOT NULL CHECK (level BETWEEN 0 AND 2),
    changed_at  iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (user_id, level)
);

-- P4. Single-use invite so a new user sets their OWN password (admins never type one).
-- Deliberately NOT folded into signing_tokens: that table's st_exactly_one_target CHECK and
-- race-free redemption are security-critical and covered by tests; widening them for an
-- unrelated purpose would put attestation at risk. Same discipline though — hash only.
CREATE TABLE user_invites (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  text NOT NULL UNIQUE,
    invited_by_user_id text REFERENCES users(id),
    issued_at   iso_ts NOT NULL DEFAULT now_iso(),
    expires_at  iso_ts NOT NULL,
    consumed_at iso_ts,
    revoked_at  iso_ts
);
CREATE INDEX ix_user_invites_user ON user_invites (user_id) WHERE consumed_at IS NULL;

-- KEPT AS A REAL TABLE, NOT folded into people and NOT replaced by a view.
-- Rationale (this reverses draft B's headline decision): api/database.py reflects with
-- MetaData.reflect(bind=engine), whose `views` argument defaults to False — a view named
-- tenant_members is NOT reflected, so t("tenant_members") raises KeyError at import and
-- all 8 read sites (api/auth.py:159, tasks.py:20, assessments.py:31, evidence.py:87,
-- policies.py:94, templates.py:102/182, tasks_engine.py:68) die. Separately,
-- scripts/init_db.py and tests/conftest.py used to do a POSITIONAL
-- `INSERT INTO tenant_members VALUES (:i,:t,:u,:r,:c)`, which made any new column a
-- landmine. P4-S1 converted every such call site to a NAMED insert, so columns may now be
-- appended safely — keep them at the END and keep the inserts named.
--
-- P4-S2: `role_id` is the real authorisation source. The legacy `role` text column is kept
-- so pre-P4 rows (and fixtures that don't set role_id) still resolve — see
-- api/permissions.py LEGACY_ROLE_MAP.
CREATE TABLE tenant_members (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id),
    user_id     text NOT NULL REFERENCES users(id),
    role        text NOT NULL CHECK (role IN ('admin', 'manager', 'member')),
    created_at  iso_ts NOT NULL DEFAULT now_iso(),
    role_id     text,                             -- FK added in CROSS-LAYER section
    UNIQUE (tenant_id, user_id),
    UNIQUE (id, tenant_id),
    UNIQUE (user_id, tenant_id)   -- referenced by people.user_id's composite FK
);

-- P4-S3. Admin-editable dropdown vocabularies (risk category, vendor category, asset
-- subtype, data type, incident category). Deliberately a TABLE rather than CHECK
-- constraints: these lists change per customer and adding a value must not need a
-- migration. True state machines (status, severity, classification) stay as CHECKs.
-- `kind` is validated against api/vocabularies.KINDS, not here.
CREATE TABLE lookup_values (
    id         text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind       text NOT NULL,
    value      text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    is_active  integer NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (tenant_id, kind, value)
);
CREATE INDEX ix_lookup_kind ON lookup_values (tenant_id, kind, sort_order);

-- P4-S2. RBAC: a role is a named bundle of (module, action) permissions, per organisation.
-- System roles (is_system=1) are seeded for every org and cannot be deleted; admins may add
-- their own. Permissions are deliberately module x action only — no record-level ownership.
CREATE TABLE roles (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        text NOT NULL,
    description text,
    is_system   integer NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
    created_at  iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (tenant_id, name),
    UNIQUE (id, tenant_id)
);

-- The checkbox matrix, one row per ticked box. The vocabulary lives in api/permissions.py
-- (MODULES x ACTIONS); it is intentionally NOT a CHECK constraint here, so adding a module
-- does not require a migration.
CREATE TABLE role_permissions (
    role_id  text NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    module   text NOT NULL,
    action   text NOT NULL,
    PRIMARY KEY (role_id, module, action)
);

-- M8. A HUMAN — with or without a login. The accountable owner of everything in L1-L3.
-- Ownership points here (survives account deletion, works for CMS/field engineers who
-- will never log in — D-SIGN). Actorship keeps pointing at users/tenant_members.
CREATE TABLE people (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id             text,                       -- optional bridge to a login
    full_name           text NOT NULL,
    email               email_addr NOT NULL,
    employee_number     text,
    department          text,
    position            text,
    manager_id          text,
    contract_start_date iso_ts,
    contract_end_date   iso_ts,                     -- drives auto-INACTIVE (v_people_effective_state)
    state               text NOT NULL DEFAULT 'ACTIVE' CHECK (state IN ('ACTIVE', 'INACTIVE')),
    source              text NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'IMPORT')),
    created_at          iso_ts NOT NULL DEFAULT now_iso(),
    updated_at          iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, email),
    CONSTRAINT people_no_self_manage CHECK (manager_id IS DISTINCT FROM id),
    FOREIGN KEY (manager_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE SET NULL (manager_id),
    -- a person's login must already be a member of the SAME tenant
    FOREIGN KEY (user_id, tenant_id) REFERENCES tenant_members (user_id, tenant_id) ON DELETE SET NULL (user_id)
);
CREATE UNIQUE INDEX uq_people_user ON people (tenant_id, user_id) WHERE user_id IS NOT NULL;

-- Shared blob vault. sha256 feeds the evidence pack and the e-signature chain.
--
-- `purpose` (P6-S5) exists for one reason, and it is a security one. This table is shared by
-- evidence uploads, policy files, asset photos, contracts, templates, published PDFs and org
-- logos — and most of those routes store `file.content_type`, the value the BROWSER claimed
-- (see evidence.py, registers.py, templates.py). So a route that authorises "any image in my
-- tenant" by looking at `mime_type` is a cross-module read: upload a confidential contract
-- declaring `Content-Type: image/png` with `evidence.add`, and anyone holding `documents.view`
-- can fetch it back through the document-image route without ever holding `evidence.view`.
--
-- Authorising on `purpose` instead means a route can only ever reach rows that its own
-- sniffing upload path created. Adding a value here is therefore a deliberate act: it widens
-- what some route can serve.
CREATE TABLE files (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id),
    storage_key           text NOT NULL UNIQUE,
    original_name         text NOT NULL,
    mime_type             text,
    size_bytes            integer,
    sha256                text,
    purpose               text NOT NULL DEFAULT 'GENERIC'
                          CHECK (purpose IN ('GENERIC', 'DOC_IMAGE')),
    uploaded_by_member_id text REFERENCES tenant_members(id),
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id)
);


-- =====================================================================================
-- L1 · PROGRAM SUBSTRATE (M10/M12/M14) — the registers banks actually demand
-- =====================================================================================

-- M12. Security awareness / role training definitions (KSL #13, AnnexC #59, VRA #22.1).
CREATE TABLE trainings (
    id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    title           text NOT NULL,
    description     text,
    cadence_months  integer CHECK (cadence_months > 0),
    owner_person_id text,
    status          text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RETIRED')),
    created_at      iso_ts NOT NULL DEFAULT now_iso(),
    updated_at      iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- Who owes which training by when; completion is evidenced, not asserted.
CREATE TABLE training_assignments (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    training_id  text NOT NULL,
    person_id    text NOT NULL,
    due_at       iso_ts,
    completed_at iso_ts,
    evidence_id  text,                              -- FK added in CROSS-LAYER section
    status       text NOT NULL DEFAULT 'ASSIGNED'
                     CHECK (status IN ('ASSIGNED', 'COMPLETED', 'OVERDUE', 'WAIVED')),
    created_at   iso_ts NOT NULL DEFAULT now_iso(),
    updated_at   iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (training_id, person_id, due_at),
    CONSTRAINT ta_done_needs_ts CHECK (status <> 'COMPLETED' OR completed_at IS NOT NULL),
    FOREIGN KEY (training_id, tenant_id) REFERENCES trainings (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (person_id, tenant_id)   REFERENCES people    (id, tenant_id) ON DELETE RESTRICT
);

-- M10. The risk register — the #1 bank ask. Inherent -> treatment -> residual.
-- 1-5 scales here vs findings' 1-3 (AnnexC): deliberate, see MIGRATION note 8.
CREATE TABLE risks (
    id                   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id            text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    reference            text,
    title                text NOT NULL,
    description          text,
    category             text,
    owner_person_id      text,
    reported_by_person_id text,                    -- P4-S7: who raised it
    reviewed_by_person_id text,                    -- P4-S7: who last reviewed the scoring
    inherent_likelihood  smallint CHECK (inherent_likelihood BETWEEN 1 AND 5),
    inherent_impact      smallint CHECK (inherent_impact BETWEEN 1 AND 5),
    inherent_score       smallint GENERATED ALWAYS AS (inherent_likelihood * inherent_impact) STORED,
    residual_likelihood  smallint CHECK (residual_likelihood BETWEEN 1 AND 5),
    residual_impact      smallint CHECK (residual_impact BETWEEN 1 AND 5),
    residual_score       smallint GENERATED ALWAYS AS (residual_likelihood * residual_impact) STORED,
    treatment            text CHECK (treatment IN ('MITIGATED', 'ACCEPTED', 'AVOIDED', 'TRANSFERRED')),
    note                 text,
    status               text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    next_review_at       iso_ts,
    created_at           iso_ts NOT NULL DEFAULT now_iso(),
    updated_at           iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, reference),
    FOREIGN KEY (owner_person_id, tenant_id)       REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (reported_by_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (reviewed_by_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- What treats / is threatened by a risk. Polymorphic-by-columns, arity-checked.
CREATE TABLE risk_links (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    risk_id        text NOT NULL,
    target_kind    text NOT NULL CHECK (target_kind IN ('CONTROL', 'DOCUMENT', 'OBLIGATION',
                                                        'ASSET', 'THIRD_PARTY', 'INCIDENT')),
    control_id     text,
    document_id    text,
    obligation_id  text,
    asset_id       text,
    third_party_id text,
    incident_id    text,
    note           text,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT rl_exactly_one CHECK (num_nonnulls(control_id, document_id, obligation_id,
                                                  asset_id, third_party_id, incident_id) = 1),
    CONSTRAINT rl_kind_matches CHECK (
        (target_kind = 'CONTROL'     AND control_id     IS NOT NULL) OR
        (target_kind = 'DOCUMENT'    AND document_id    IS NOT NULL) OR
        (target_kind = 'OBLIGATION'  AND obligation_id  IS NOT NULL) OR
        (target_kind = 'ASSET'       AND asset_id       IS NOT NULL) OR
        (target_kind = 'THIRD_PARTY' AND third_party_id IS NOT NULL) OR
        (target_kind = 'INCIDENT'    AND incident_id    IS NOT NULL)),
    FOREIGN KEY (risk_id, tenant_id) REFERENCES risks (id, tenant_id) ON DELETE CASCADE
);

-- M10. Asset tracker (VRA/AnnexC ask). PHYSICAL|VIRTUAL, owned, classified.
CREATE TABLE assets (
    id                   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id            text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name                 text NOT NULL,
    description          text,
    asset_type           text NOT NULL DEFAULT 'VIRTUAL' CHECK (asset_type IN ('PHYSICAL', 'VIRTUAL')),
    owner_person_id      text,
    quantity             integer NOT NULL DEFAULT 1 CHECK (quantity >= 0),
    data_types_stored    text[] NOT NULL DEFAULT '{}',
    criticality          text CHECK (criticality IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    location             text,
    -- P4-S7: type-aware detail. Real nullable columns, not a JSONB bag — this schema has
    -- no jsonb column anywhere, and the PHYSICAL/VIRTUAL field set is closed, so columns
    -- reach the API for free (list_assets does select(a)) and keep their type discipline.
    -- Deliberately NO cross-field CHECK: a PATCH flipping asset_type while the other
    -- side's columns are still populated would 500 on a CheckViolation. Shape is enforced
    -- in api/routers/registers.py's _validate_asset against the MERGED row, the same way
    -- findings deliberately has no "closed needs root_cause" CHECK (see its comment).
    subtype              text,                      -- lookup_values kind='asset_subtype'
    manufacturer         text,                      -- PHYSICAL
    model                text,                      -- PHYSICAL
    serial_number        text,                      -- PHYSICAL
    hostname             text,                      -- VIRTUAL
    ip_address           text,                      -- VIRTUAL; text not inet — a typo must
                                                    -- be a 400, not an opaque 22P02 500
    cloud_provider       text,                      -- VIRTUAL
    service_url          text,                      -- VIRTUAL
    vendor_third_party_id text,                     -- FK added in CROSS-LAYER section
    -- P4-S7: a photograph of the physical unit. Goes into `files`, not `evidence` — same
    -- reasoning as third_party_agreements.file_id: a rack photo is an attribute of the
    -- asset, not a dated compliance artifact with its own expiry to track.
    photo_file_id        text,
    created_at           iso_ts NOT NULL DEFAULT now_iso(),
    updated_at           iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (photo_file_id, tenant_id)   REFERENCES files  (id, tenant_id) ON DELETE RESTRICT
);

-- M10. Data inventory (classification drives the SoA + document classification).
CREATE TABLE data_items (
    id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name            text NOT NULL,
    description     text,
    data_type       text,                           -- P4-S7: lookup_values kind='data_type'
    owner_person_id text,
    classification  text NOT NULL DEFAULT 'INTERNAL'
                        CHECK (classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'SECRET')),
    retention_note  text,
    created_at      iso_ts NOT NULL DEFAULT now_iso(),
    updated_at      iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- M10. Vendors. parent_third_party_id = the 4th-party chain banks ask us to disclose.
CREATE TABLE third_parties (
    id                       text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id                text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name                     text NOT NULL,
    legal_name               text,
    parent_third_party_id    text,                  -- self-FK: our vendor's vendor
    category                 text,
    countries                text[] NOT NULL DEFAULT '{}',
    certifications           text[] NOT NULL DEFAULT '{}',
    business_owner_person_id text,
    security_owner_person_id text,
    criticality              text CHECK (criticality IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    status                   text NOT NULL DEFAULT 'ACTIVE'
                                 CHECK (status IN ('ACTIVE', 'OFFBOARDING', 'TERMINATED')),
    created_at               iso_ts NOT NULL DEFAULT now_iso(),
    updated_at               iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT tp_no_self_parent CHECK (parent_third_party_id IS DISTINCT FROM id),
    FOREIGN KEY (parent_third_party_id, tenant_id)    REFERENCES third_parties (id, tenant_id) ON DELETE SET NULL (parent_third_party_id),
    FOREIGN KEY (business_owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (security_owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- DPA/BAA/NDA/SLA/MSA with expiry — an expiring-agreement queue banks ask for by name.
CREATE TABLE third_party_agreements (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    third_party_id text NOT NULL,
    kind           text NOT NULL CHECK (kind IN ('DPA', 'BAA', 'NDA', 'SLA', 'MSA', 'OTHER')),
    reference      text,
    valid_from     iso_ts,
    valid_until    iso_ts,
    file_id        text,
    notes          text,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (third_party_id, tenant_id) REFERENCES third_parties (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (file_id, tenant_id)        REFERENCES files (id, tenant_id) ON DELETE RESTRICT
);

-- Vendor security assessments — expires_at is the point (a 2023 review is not 2026 assurance).
CREATE TABLE third_party_assessments (
    id                text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    third_party_id    text NOT NULL,
    assessed_at       iso_ts,
    expires_at        iso_ts,
    data_sensitivity  text CHECK (data_sensitivity IN ('NONE', 'LOW', 'MEDIUM', 'HIGH')),
    business_impact   text CHECK (business_impact IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    outcome           text CHECK (outcome IN ('PASS', 'PASS_WITH_ACTIONS', 'FAIL')),
    notes             text,
    evidence_id       text,                         -- FK added in CROSS-LAYER section
    created_at        iso_ts NOT NULL DEFAULT now_iso(),
    updated_at        iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (third_party_id, tenant_id) REFERENCES third_parties (id, tenant_id) ON DELETE CASCADE
);

-- M10. Legal/contractual obligations. regulator='RBI' is the driver here.
CREATE TABLE obligations (
    id               text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    area             text,
    source           text,
    requirement      text NOT NULL,
    regulator        text,
    type             text CHECK (type IN ('LEGAL', 'CONTRACTUAL')),
    owner_person_id  text,
    clause_id        text,                          -- FK added in CROSS-LAYER section
    last_review_date iso_ts,
    next_review_date iso_ts,
    status           text NOT NULL DEFAULT 'PARTIALLY_COMPLIANT'
                         CHECK (status IN ('COMPLIANT', 'PARTIALLY_COMPLIANT', 'NON_COMPLIANT')),
    created_at       iso_ts NOT NULL DEFAULT now_iso(),
    updated_at       iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- M10. Incident tracker WITH RCA — banks ask for the RCA, not the ticket.
CREATE TABLE incidents (
    id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    reference       text,
    title           text NOT NULL,
    description     text,
    severity        text CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    -- P5-S4. Free text, NOT a CHECK: the allowed values live in `lookup_values` under the
    -- `incident_category` kind (api/vocabularies.py), so an admin can extend them from the
    -- Masters screen without a migration. Same shape as risks.category / assets.subtype.
    -- The vocabulary was seeded in P4-S3 and sat unused because the column never existed.
    category        text,
    detected_at     iso_ts,
    resolved_at     iso_ts,
    root_cause      text,
    -- P4-S7. Narrative only. TRACKED corrective actions (owner / due date / status) belong
    -- in `findings`, which is already the org-level CAPA register — a second CAPA engine
    -- here would be a design mistake. Deliberately NOT added to inc_closed_needs_rca:
    -- one close gate (root_cause) is enough, and a second would change what CLOSED means
    -- for every incident already in the table.
    resolution      text,
    corrective_action text,
    lessons_learnt  text,
    owner_person_id text,
    status          text NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'CLOSED')),
    created_at      iso_ts NOT NULL DEFAULT now_iso(),
    updated_at      iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, reference),
    CONSTRAINT inc_closed_needs_rca CHECK (status <> 'CLOSED' OR root_cause IS NOT NULL),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- P4-S7. The incident timeline: what happened, when, and who said so.
--
-- APPEND-ONLY via deny_update(), NOT deny_change(): this table cascades from incidents, and
-- denying DELETE too would make an incident permanently undeletable with an opaque error —
-- the M6 mistake documented at the append-only trigger block below. Consequence, accepted
-- deliberately: a timeline entry can never be edited. The UI offers "add a correction",
-- not an edit pencil, because tamper-evidence is the point for a bank auditor.
--
-- occurred_at is the REAL-WORLD time and is backdatable ("containment at 02:15"); created_at
-- is when it was typed in. Both matter in an incident report.
--
-- The author is a USER, not a person: people.user_id is nullable, so a person FK would be
-- unfillable for the common case. Mirrors review_messages.author_user_id.
CREATE TABLE incident_events (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    incident_id    text NOT NULL,
    event_type     text NOT NULL CHECK (event_type IN ('DETECTED', 'CONTAINMENT', 'INVESTIGATION',
                       'CORRECTIVE_ACTION', 'COMMUNICATION', 'COMMENT', 'RESOLVED', 'CLOSED')),
    body           text NOT NULL,
    author_user_id text REFERENCES users(id),      -- NULL only for system-generated events
    author_kind    text NOT NULL DEFAULT 'member' CHECK (author_kind IN ('member', 'system')),
    occurred_at    iso_ts NOT NULL DEFAULT now_iso(),
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    -- Insertion order, and the ONLY reliable tiebreak this table has. Both now_iso()
    -- implementations (SQL and api/util.py) are second-resolution, so two entries logged
    -- in the same second tie on occurred_at AND created_at — leaving Postgres free to
    -- return the timeline in any order it likes. An append-only incident narrative that
    -- silently reorders itself is worse than no narrative at all, so reads sort by
    -- (occurred_at, seq): user-stated chronology first, then the order it was written.
    seq            bigserial NOT NULL,
    UNIQUE (id, tenant_id),
    CONSTRAINT ie_member_has_author CHECK (author_kind <> 'member' OR author_user_id IS NOT NULL),
    FOREIGN KEY (incident_id, tenant_id) REFERENCES incidents (id, tenant_id) ON DELETE CASCADE
);

-- M14. Privileged access review campaigns (explicit bank ask).
CREATE TABLE access_review_campaigns (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name         text NOT NULL,
    description  text,
    status       text NOT NULL DEFAULT 'DRAFT'
                     CHECK (status IN ('DRAFT', 'IN_PROGRESS', 'PENDING_ACTIONS', 'COMPLETED', 'CANCELLED')),
    started_at   iso_ts,
    completed_at iso_ts,
    document_id  text,                              -- FK added in CROSS-LAYER section
    created_at   iso_ts NOT NULL DEFAULT now_iso(),
    updated_at   iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id)
);

-- CSV-first: no connectors needed. The upload lands in the file vault (sha256 for free).
CREATE TABLE access_review_sources (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    campaign_id    text NOT NULL,
    name           text NOT NULL,
    source_file_id text,                            -- the uploaded CSV, not an inline blob
    connector_ref  text,
    imported_at    iso_ts,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (campaign_id, tenant_id)    REFERENCES access_review_campaigns (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (source_file_id, tenant_id) REFERENCES files (id, tenant_id) ON DELETE RESTRICT
);

-- One account row under review. person_id NULL = unmatched account (itself a finding).
CREATE TABLE access_review_entries (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    campaign_id    text NOT NULL,
    source_id      text NOT NULL,
    person_id      text,
    email          text,
    roles          text[] NOT NULL DEFAULT '{}',
    is_admin       boolean NOT NULL DEFAULT false,
    mfa_status     text CHECK (mfa_status IN ('ENABLED', 'DISABLED', 'UNKNOWN')),
    account_type   text NOT NULL DEFAULT 'USER' CHECK (account_type IN ('USER', 'SERVICE_ACCOUNT')),
    last_login     iso_ts,
    flag_count     integer NOT NULL DEFAULT 0,
    decision       text NOT NULL DEFAULT 'PENDING'
                       CHECK (decision IN ('PENDING', 'APPROVED', 'REVOKE', 'DEFER', 'ESCALATE')),
    decision_note  text,
    decided_by_person_id text,
    decided_at     iso_ts,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    updated_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT are_decided_needs_who CHECK (decision = 'PENDING'
                                            OR (decided_by_person_id IS NOT NULL AND decided_at IS NOT NULL)),
    FOREIGN KEY (campaign_id, tenant_id)          REFERENCES access_review_campaigns (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id, tenant_id)            REFERENCES access_review_sources   (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (person_id, tenant_id)            REFERENCES people (id, tenant_id) ON DELETE SET NULL (person_id),
    FOREIGN KEY (decided_by_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- The 15 flags, one row each + its reason. Replaces the docs' parallel flags[]/flag_reasons[]:
-- "element i matches element i" is not expressible as a constraint and drifts silently.
CREATE TABLE access_review_entry_flags (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    entry_id  text NOT NULL,
    code      text NOT NULL,                        -- e.g. NO_MFA, DORMANT_90D, ORPHANED, ADMIN_NO_MFA
    reason    text,
    PRIMARY KEY (entry_id, code),
    FOREIGN KEY (entry_id, tenant_id) REFERENCES access_review_entries (id, tenant_id) ON DELETE CASCADE
);

-- Append-only decision history: who decided what, when, and why. Never rewritten.
CREATE TABLE access_review_decisions (
    id               text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    entry_id         text NOT NULL,
    decision         text NOT NULL CHECK (decision IN ('APPROVED', 'REVOKE', 'DEFER', 'ESCALATE')),
    note             text,
    decided_by_person_id text,
    decided_at       iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (entry_id, tenant_id)             REFERENCES access_review_entries (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (decided_by_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);


-- =====================================================================================
-- L2 · CONTROLS (ported: "our measures") & FRAMEWORK CLAUSES (new) & SoA
-- =====================================================================================

-- PORTED. Control taxonomy (AM, NI, ...). One nesting level in practice.
CREATE TABLE domains (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id),
    parent_id   text,
    code        text,
    name        text NOT NULL,
    description text,
    sort_order  integer NOT NULL DEFAULT 0,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (parent_id, tenant_id) REFERENCES domains (id, tenant_id)
);

-- PORTED, name kept per D-TERMS. OUR 95 reusable implementations (industry: "measures").
-- framework_refs (JSON tags) is RETAINED as-is and additionally normalised into
-- control_clause_map at migration — zero API refs, so the column is free to keep as a
-- provenance record rather than deleted (draft A dropped it; that loses the import audit trail).
CREATE TABLE controls (
    id                   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id            text NOT NULL REFERENCES tenants(id),
    domain_id            text NOT NULL,
    code                 text NOT NULL,
    statement            text NOT NULL,
    guidance             text,
    lifecycle            text NOT NULL CHECK (lifecycle IN ('one_time', 'recurring', 'per_audit')),
    recurrence_months    integer,
    applicability        text NOT NULL DEFAULT 'applicable'
                             CHECK (applicability IN ('applicable', 'not_applicable')),
    na_justification     text,
    reactivation_trigger text,
    stock_response       text CHECK (stock_response IN ('yes', 'partial', 'no', 'na')),
    stock_comment        text,
    owner_member_id      text REFERENCES tenant_members(id),
    owner_person_id      text,                      -- NEW: accountable human (D-SIGN); member kept for the app
    framework_refs       text,                      -- legacy JSON tags, superseded by control_clause_map
    status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at           iso_ts NOT NULL DEFAULT now_iso(),
    updated_at           iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id),
    CONSTRAINT controls_na_needs_reason CHECK (applicability <> 'not_applicable'
                                               OR na_justification IS NOT NULL),
    CONSTRAINT controls_recurring_needs_months CHECK (lifecycle <> 'recurring'
                                                      OR recurrence_months IS NOT NULL),
    FOREIGN KEY (domain_id, tenant_id)       REFERENCES domains (id, tenant_id),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people  (id, tenant_id) ON DELETE RESTRICT
);

-- PORTED + EXTENDED. "This control must be evidenced by X, every N months."
-- cadence_months/is_mandatory are NEW and load-bearing: they are what make v_evidence_gaps
-- able to call undated evidence STALE (see the M1 fix).
CREATE TABLE control_evidence_requirements (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    control_id     text NOT NULL,
    evidence_type  text NOT NULL,                   -- Phase 0 Doc 2 §3 vocabulary
    cadence_months integer CHECK (cadence_months > 0),   -- NEW: how often it must be refreshed
    is_mandatory   boolean NOT NULL DEFAULT true,        -- NEW
    note           text,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (control_id, tenant_id) REFERENCES controls (id, tenant_id) ON DELETE CASCADE
);

-- M13. ISO 27001 / SOC 2 / hand-authored RBI. Tenant-owned copies (see MIGRATION note 7).
CREATE TABLE frameworks (
    id         text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    code       text NOT NULL,                       -- ISO27001-2022 | SOC2 | RBI-ITO
    name       text NOT NULL,
    version    text,
    source     text NOT NULL DEFAULT 'IMPORTED' CHECK (source IN ('AUTHORED', 'IMPORTED')),
    created_at iso_ts NOT NULL DEFAULT now_iso(),
    updated_at iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, code)
);

-- An EXTERNAL requirement: A.5.16, CC6.1, an RBI clause. (Industry calls this a "Control".)
CREATE TABLE framework_clauses (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    framework_id text NOT NULL,
    ref          text NOT NULL,
    title        text NOT NULL,
    description  text,
    sort_order   integer NOT NULL DEFAULT 0,
    UNIQUE (id, tenant_id),
    UNIQUE (framework_id, ref),
    FOREIGN KEY (framework_id, tenant_id) REFERENCES frameworks (id, tenant_id) ON DELETE CASCADE
);

-- D-FRAMEWORK: MANY-TO-MANY. One control satisfies A.5.16 AND CC6.1 AND an RBI clause.
-- This is the explicit fix for Probo's single-framework Control.
CREATE TABLE control_clause_map (
    tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    control_id text NOT NULL,
    clause_id  text NOT NULL,
    note       text,
    created_at iso_ts NOT NULL DEFAULT now_iso(),
    PRIMARY KEY (control_id, clause_id),
    FOREIGN KEY (control_id, tenant_id) REFERENCES controls          (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (clause_id, tenant_id)  REFERENCES framework_clauses (id, tenant_id) ON DELETE CASCADE
);

-- M10b. Which controls satisfy which legal/contractual obligation (RBI etc.). M2M, so one
-- control can answer several obligations and one obligation lean on several controls. Feeds
-- the SoA's reason-for-inclusion in Sprint 7.
CREATE TABLE control_obligation_map (
    tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    control_id    text NOT NULL,
    obligation_id text NOT NULL,
    note          text,
    created_at    iso_ts NOT NULL DEFAULT now_iso(),
    PRIMARY KEY (control_id, obligation_id),
    FOREIGN KEY (control_id, tenant_id)    REFERENCES controls    (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (obligation_id, tenant_id) REFERENCES obligations (id, tenant_id) ON DELETE CASCADE
);

-- M13. The SoA header; document_id is the published, approved, signed rendering.
CREATE TABLE statements_of_applicability (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name         text NOT NULL,
    framework_id text NOT NULL,
    document_id  text,                              -- FK added in CROSS-LAYER section
    created_at   iso_ts NOT NULL DEFAULT now_iso(),
    updated_at   iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (framework_id, tenant_id) REFERENCES frameworks (id, tenant_id) ON DELETE RESTRICT
);

-- Per-clause applicable/justification. reason-for-inclusion is DERIVED, not stored
-- (v_soa_reason_for_inclusion) — it falls out of the risk/obligation graph.
CREATE TABLE applicability_statements (
    id            text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    soa_id        text NOT NULL,
    clause_id     text NOT NULL,
    applicable    boolean NOT NULL DEFAULT true,
    justification text,
    UNIQUE (id, tenant_id),
    UNIQUE (soa_id, clause_id),
    CONSTRAINT as_na_needs_reason CHECK (applicable OR justification IS NOT NULL),
    FOREIGN KEY (soa_id, tenant_id)    REFERENCES statements_of_applicability (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (clause_id, tenant_id) REFERENCES framework_clauses (id, tenant_id) ON DELETE CASCADE
);


-- =====================================================================================
-- L3 · DOCUMENTS (M9/M11) — AUTHORED policies + GENERATED registers
-- =====================================================================================

-- D-GENERATED. write_mode AUTHORED = a human writes markdown; GENERATED = a register
-- renders itself. generator_key is CHECK-constrained: an unconstrained key silently
-- falls through the drift CASE and never flags (the M2 fix).
CREATE TABLE documents (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    title                 text NOT NULL,
    description           text,
    document_type         text NOT NULL DEFAULT 'POLICY'
                              CHECK (document_type IN ('GOVERNANCE', 'POLICY', 'PROCEDURE', 'PLAN',
                                                       'REGISTER', 'RECORD', 'REPORT', 'TEMPLATE', 'SOA')),
    -- P7-S5: was CHECKed to ('PUBLIC','INTERNAL','CONFIDENTIAL','SECRET'), same as
    -- data_items.classification below still is. Deliberately widened to a free-text,
    -- admin-editable vocabulary (lookup_values kind='document_classification',
    -- api/domain/vocabularies.py) so an org can add e.g. RESTRICTED without a migration —
    -- Admin/Masters had no way to manage this field at all. data_items.classification is
    -- untouched: only the request that named Document Classification changes.
    classification        text NOT NULL DEFAULT 'INTERNAL',
    write_mode            text NOT NULL DEFAULT 'AUTHORED' CHECK (write_mode IN ('AUTHORED', 'GENERATED')),
    generator_key         text CHECK (generator_key IN ('risk_list', 'asset_list', 'data_list',
                                                        'third_party_list', 'obligation_list',
                                                        'incident_list', 'finding_list',
                                                        'training_records', 'access_review', 'soa')),
    owner_person_id       text NOT NULL,            -- Probo lacks this. Every doc has a human.
    review_cadence_months integer CHECK (review_cadence_months > 0),
    next_review_at        iso_ts,
    current_published_version_id text,              -- FK added in CROSS-LAYER section
    status                text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    trust_visibility      text NOT NULL DEFAULT 'NONE'
                              CHECK (trust_visibility IN ('NONE', 'PRIVATE', 'PUBLIC')),
    legacy_policy_id      text,                     -- provenance from the folded policies table
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    updated_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT doc_generated_needs_key CHECK ((write_mode = 'GENERATED') = (generator_key IS NOT NULL)),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- major.minor, markdown content, frozen at publish. The published row IS the snapshot —
-- no snapshots table (Probo built one and dropped it).
-- source_row_count/source_max_updated_at are captured at publish for GENERATED docs: together
-- they detect DELETIONS from a register, which a max(updated_at) probe cannot (the M2 fix).
CREATE TABLE document_versions (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    document_id           text NOT NULL,
    major                 integer NOT NULL CHECK (major >= 0),
    minor                 integer NOT NULL CHECK (minor >= 0),
    version_label         text GENERATED ALWAYS AS (major::text || '.' || minor::text) STORED,
    content               text NOT NULL DEFAULT '',
    -- P4-S4: authored content is HTML now (TipTap). Everything written before that sprint is
    -- markdown and STAYS markdown — sanitising or converting it would change content_sha256,
    -- which electronic_signatures.file_sha256 pins, orphaning every attestation. The format is
    -- an explicit column rather than sniffed: `Use the <b>badge</b>` is valid markdown that any
    -- heuristic reads as HTML, and the meaning of already-signed bytes must not be a guess.
    content_format        text NOT NULL DEFAULT 'MARKDOWN'
                              -- P5-S2: SHEET holds a JSON grid (see api/render.py
                              -- sheet_json_to_html), not markup — never sanitised as HTML,
                              -- same as MARKDOWN is deliberately left raw.
                              CHECK (content_format IN ('MARKDOWN', 'HTML', 'SHEET')),
    content_sha256        text GENERATED ALWAYS AS (sha256_hex(content)) STORED,
    changelog             text,
    status                text NOT NULL DEFAULT 'DRAFT'
                              CHECK (status IN ('DRAFT', 'PENDING_APPROVAL', 'PUBLISHED', 'SUPERSEDED')),
    published_at          iso_ts,
    file_id               text,                     -- rendered PDF (WeasyPrint)
    source_row_count      integer,                  -- GENERATED docs: register size at publish
    source_max_updated_at iso_ts,                   -- GENERATED docs: register high-water mark
    created_by_member_id  text REFERENCES tenant_members(id),
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    updated_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (document_id, major, minor),
    CONSTRAINT dv_published_needs_ts CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (file_id, tenant_id)     REFERENCES files (id, tenant_id) ON DELETE RESTRICT
);

-- D-APPROVAL. threshold_required = M of N. Probo's "quorum" is unanimity; that's the bug.
CREATE TABLE document_approvals (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    document_version_id text NOT NULL,
    threshold_required  integer NOT NULL CHECK (threshold_required >= 1),
    status              text NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
    opened_at           iso_ts NOT NULL DEFAULT now_iso(),
    closed_at           iso_ts,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions (id, tenant_id) ON DELETE CASCADE
);

-- One approver's verdict, optionally e-signed.
CREATE TABLE document_approval_decisions (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    approval_id         text NOT NULL,
    approver_person_id  text NOT NULL,
    state               text NOT NULL DEFAULT 'PENDING'
                            CHECK (state IN ('PENDING', 'APPROVED', 'REJECTED', 'ABSTAINED')),
    comment             text,
    e_signature_id      text,                       -- FK added in CROSS-LAYER section
    decided_at          iso_ts,
    created_at          iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (approval_id, approver_person_id),
    CONSTRAINT dad_decided_needs_ts CHECK (state = 'PENDING' OR decided_at IS NOT NULL),
    FOREIGN KEY (approval_id, tenant_id)        REFERENCES document_approvals (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (approver_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

-- D-AUDIENCE. Probo cannot express "everyone". rule+value; EXPLICIT uses person_id.
CREATE TABLE document_audiences (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    document_id text NOT NULL,
    rule        text NOT NULL CHECK (rule IN ('ALL_EMPLOYEES', 'DEPARTMENT', 'EXPLICIT')),
    value       text,                               -- department name when rule=DEPARTMENT
    person_id   text,                               -- the person when rule=EXPLICIT
    created_at  iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT da_rule_shape CHECK (
        (rule = 'ALL_EMPLOYEES' AND value IS NULL     AND person_id IS NULL) OR
        (rule = 'DEPARTMENT'    AND value IS NOT NULL AND person_id IS NULL) OR
        (rule = 'EXPLICIT'      AND value IS NULL     AND person_id IS NOT NULL)),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (person_id, tenant_id)   REFERENCES people    (id, tenant_id) ON DELETE CASCADE
);

-- D-SIGN evidence chain. Immutable once written. NOT tied to a login: signer_person_id
-- may be a person with user_id IS NULL, and signer_label carries an external signer's email.
CREATE TABLE electronic_signatures (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    signer_person_id    text,                       -- NULL for a purely external signer (bank NDA)
    signer_label        text,                       -- email/name as presented, frozen
    signer_name         text NOT NULL,
    consent_text        text NOT NULL,              -- the exact wording shown, frozen
    signer_ip           inet,
    signer_user_agent   text,
    file_sha256         text,                       -- what was signed (document_versions.content_sha256)
    certificate_file_id text,                       -- signing certificate PDF (TSA deferred)
    signed_at           iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT esig_has_identity CHECK (signer_person_id IS NOT NULL OR signer_label IS NOT NULL),
    FOREIGN KEY (signer_person_id, tenant_id)    REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (certificate_file_id, tenant_id) REFERENCES files  (id, tenant_id) ON DELETE RESTRICT
);

-- The attestation request + result. due_at is NEW: without it an attestation can never be
-- overdue, only older, and D-AUDIENCE's queue has nothing to sort on.
CREATE TABLE document_signatures (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    document_version_id text NOT NULL,
    person_id           text NOT NULL,
    state               text NOT NULL DEFAULT 'REQUESTED'
                            CHECK (state IN ('REQUESTED', 'SIGNED', 'EXEMPT')),
    requested_at        iso_ts NOT NULL DEFAULT now_iso(),
    due_at              iso_ts,
    signed_at           iso_ts,
    e_signature_id      text,
    exempt_reason       text,
    UNIQUE (id, tenant_id),
    UNIQUE (document_version_id, person_id),
    CONSTRAINT ds_signed_needs_esig CHECK (state <> 'SIGNED'
                                           OR (e_signature_id IS NOT NULL AND signed_at IS NOT NULL)),
    CONSTRAINT ds_exempt_needs_reason CHECK (state <> 'EXEMPT' OR exempt_reason IS NOT NULL),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (person_id, tenant_id)           REFERENCES people (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (e_signature_id, tenant_id)      REFERENCES electronic_signatures (id, tenant_id) ON DELETE RESTRICT
);

-- D-SIGN: the MAGIC LINK. We store sha256(token) only — the raw 256-bit value exists
-- solely inside the emailed URL. Redemption is one race-free statement:
--   UPDATE signing_tokens SET consumed_at = now_iso()
--    WHERE token_hash = sha256_hex($1) AND consumed_at IS NULL AND revoked_at IS NULL
--      AND expires_at > now_iso() RETURNING *;
-- Exactly one target must be bound (the M5 fix) — a token bound to nothing is a live
-- credential for an undefined action.
CREATE TABLE signing_tokens (
    id                            text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id                     text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    token_hash                    text NOT NULL UNIQUE,
    purpose                       text NOT NULL CHECK (purpose IN ('ATTEST', 'APPROVE', 'NDA')),
    document_signature_id         text,
    document_approval_decision_id text,
    trust_access_id               text,
    sent_to_email                 text NOT NULL,
    consent_text                  text NOT NULL,    -- frozen at issue, not at redemption
    issued_at                     iso_ts NOT NULL DEFAULT now_iso(),
    expires_at                    iso_ts NOT NULL,
    consumed_at                   iso_ts,
    revoked_at                    iso_ts,
    UNIQUE (id, tenant_id),
    CONSTRAINT st_exactly_one_target CHECK (num_nonnulls(document_signature_id,
                                                         document_approval_decision_id,
                                                         trust_access_id) = 1),
    CONSTRAINT st_purpose_matches CHECK (
        (purpose = 'ATTEST'  AND document_signature_id IS NOT NULL) OR
        (purpose = 'APPROVE' AND document_approval_decision_id IS NOT NULL) OR
        (purpose = 'NDA'     AND trust_access_id IS NOT NULL))
);

-- ---- control <-> document (P4-S5) ------------------------------------------------------
-- "This control is written down in that policy." Many-to-many: one policy documents dozens
-- of controls, and one control is documented by a policy AND a procedure AND a plan.
-- Mirrors control_obligation_map — the regulation side of the same fact — rather than
-- extending risk_links' polymorphic shape: a control's link targets are a closed set and
-- both sides here are plain M2M. Declared after `documents` so BOTH composite FKs are
-- inline and nothing has to defer to the CROSS-LAYER section.
CREATE TABLE control_documents (
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    control_id  text NOT NULL,
    document_id text NOT NULL,
    note        text,
    created_at  iso_ts NOT NULL DEFAULT now_iso(),
    PRIMARY KEY (control_id, document_id),
    FOREIGN KEY (control_id, tenant_id)  REFERENCES controls  (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE CASCADE
);

-- TRANSITIONAL (drop at end of M9). api/routers/policies.py + dashboard.py:73 still read
-- these. They are superseded by documents/document_versions but kept so the cutover to
-- Postgres and the fold to documents are SEPARATE, revertible steps. See MIGRATION.
CREATE TABLE policies (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id),
    title                 text NOT NULL,
    description           text,
    owner_member_id       text REFERENCES tenant_members(id),
    review_cadence_months integer,
    next_review_at        iso_ts,
    status                text NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'retired')),
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE policy_versions (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id),
    policy_id             text NOT NULL,
    version_label         text NOT NULL,
    file_id               text REFERENCES files(id),
    approved_by_member_id text REFERENCES tenant_members(id),
    approved_at           iso_ts,
    effective_from        iso_ts,
    notes                 text,
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (policy_id, tenant_id) REFERENCES policies (id, tenant_id)
);


-- =====================================================================================
-- L4 · EVIDENCE — typed, dated, EXPIRING, REQUESTED->FULFILLED  (***D-MOAT***)
-- =====================================================================================

-- PORTED + EXTENDED.
-- NAMING (the trap both drafts spotted and doc 3 §4 sets): `evidence_type` ALREADY holds the
-- Phase 0 §3 catalogue (policy_doc|certificate|report|log|screenshot) and is what the gap
-- list matches requirements on. Doc 3's "add evidence_type FILE|LINK" must NOT overwrite it —
-- that is a different fact (how it is stored). The new column is `medium`.
-- `state` DEFAULTS TO 'FULFILLED' — deliberately (the C6 fix): every existing row IS
-- fulfilled evidence, and any other default silently empties every D-MOAT queue.
CREATE TABLE evidence (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id),
    title                 text NOT NULL,
    evidence_type         text NOT NULL,            -- WHAT it is (Phase 0 catalogue)
    medium                text NOT NULL DEFAULT 'FILE' CHECK (medium IN ('FILE', 'LINK')),  -- HOW it is stored
    state                 text NOT NULL DEFAULT 'FULFILLED' CHECK (state IN ('REQUESTED', 'FULFILLED')),
    file_id               text,
    external_url          text,
    policy_version_id     text,                     -- legacy; zero API refs. -> document_version_id at M9
    document_version_id   text,                     -- NEW: when the artifact IS a document
    requirement_id        text,                     -- NEW: which control requirement this satisfies
    requested_by_control_id text,
    requested_by_member_id  text REFERENCES tenant_members(id),
    due_at                iso_ts,                   -- for state=REQUESTED
    issued_at             iso_ts,
    valid_until           iso_ts,                   -- ***freshness: a 2023 VAPT is not 2026 evidence***
    notes                 text,
    created_by_member_id  text REFERENCES tenant_members(id),
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    -- A FULFILLED artifact must actually exist. "FULFILLED but empty" is unrepresentable.
    CONSTRAINT ev_fulfilled_has_body CHECK (state <> 'FULFILLED'
        OR file_id IS NOT NULL OR external_url IS NOT NULL OR document_version_id IS NOT NULL),
    CONSTRAINT ev_medium_matches CHECK (
        (medium = 'LINK' AND external_url IS NOT NULL) OR
        (medium = 'FILE' AND (state = 'REQUESTED' OR file_id IS NOT NULL OR document_version_id IS NOT NULL))),
    FOREIGN KEY (file_id, tenant_id)                 REFERENCES files (id, tenant_id),
    FOREIGN KEY (policy_version_id, tenant_id)       REFERENCES policy_versions (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id)     REFERENCES document_versions (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (requirement_id, tenant_id)          REFERENCES control_evidence_requirements (id, tenant_id) ON DELETE SET NULL (requirement_id),
    FOREIGN KEY (requested_by_control_id, tenant_id) REFERENCES controls (id, tenant_id) ON DELETE SET NULL (requested_by_control_id)
);

-- M2M: one ISO cert backs dozens of controls.
CREATE TABLE evidence_controls (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    evidence_id text NOT NULL,
    control_id  text NOT NULL,
    PRIMARY KEY (evidence_id, control_id),
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (control_id, tenant_id)  REFERENCES controls (id, tenant_id) ON DELETE CASCADE
);

-- P4-S7 · register <-> evidence joins. Modelled on evidence_controls above rather than
-- adding a 7th kind to risk_links: that table is polymorphic-one-column-per-kind, and a new
-- kind means editing FIVE artifacts (the target_kind CHECK, rl_exactly_one, rl_kind_matches,
-- a CROSS-LAYER FK, and uq_risk_link_target's coalesce list — miss that last one and
-- concurrent double-clicks silently land duplicate rows). A join table's PK on the pair
-- dedupes for free. The semantics differ too: a risk_links row is navigation and carries a
-- note; evidence is an attachment whose natural key IS the pair.
CREATE TABLE risk_evidence (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    risk_id     text NOT NULL,
    evidence_id text NOT NULL,
    PRIMARY KEY (risk_id, evidence_id),
    FOREIGN KEY (risk_id, tenant_id)     REFERENCES risks    (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE incident_evidence (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    incident_id text NOT NULL,
    evidence_id text NOT NULL,
    PRIMARY KEY (incident_id, evidence_id),
    FOREIGN KEY (incident_id, tenant_id) REFERENCES incidents (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence  (id, tenant_id) ON DELETE CASCADE
);


-- =====================================================================================
-- L5 · TIME — the recurrence engine (***D-MOAT***, APScheduler)
-- =====================================================================================

CREATE TABLE tasks (
    id                 text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id          text NOT NULL REFERENCES tenants(id),
    control_id         text,
    policy_id          text,                        -- legacy source; document_id supersedes at M9
    document_id        text,                        -- NEW
    risk_id            text,                        -- P4-S9: a follow-up task raised on a risk
    assessment_id      text,                        -- P4-S9: a task raised out of an audit
    title              text NOT NULL,
    description        text,
    cadence_months     integer,                     -- legacy: months-only, written by generate_tasks()
                                                     -- for recurring controls. NULL = one-off.
    -- P4-S9: general recurrence for hand-created tasks. DAILY/WEEKLY are day arithmetic;
    -- MONTHLY/QUARTERLY/YEARLY are add_months() with a baked-in multiplier — QUARTERLY is
    -- mechanically MONTHLY x3, kept as its own value because that is the word a compliance
    -- calendar actually uses. No ONE_OFF value: NULL frequency (like NULL cadence_months
    -- above) already means "does not recur" — a magic enum member would just duplicate that.
    frequency          text CHECK (frequency IN ('DAILY', 'WEEKLY', 'MONTHLY', 'QUARTERLY', 'YEARLY')),
    interval_count     integer CHECK (interval_count IS NULL OR interval_count > 0),
    assignee_member_id text REFERENCES tenant_members(id),
    assignee_person_id text,                        -- NEW: staff without logins can own tasks
    next_due_at        iso_ts,
    status             text NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'paused', 'completed')),
    created_at         iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    -- frequency and interval_count travel together — both or neither.
    CONSTRAINT tasks_recurrence_shape CHECK (
        (frequency IS NULL AND interval_count IS NULL) OR
        (frequency IS NOT NULL AND interval_count IS NOT NULL)),
    -- Two recurrence systems on one row would leave "which is authoritative?" to guesswork.
    -- generate_tasks() only ever writes cadence_months; the API only ever writes frequency.
    CONSTRAINT tasks_recurrence_not_both CHECK (NOT (frequency IS NOT NULL AND cadence_months IS NOT NULL)),
    FOREIGN KEY (control_id, tenant_id)         REFERENCES controls (id, tenant_id),
    FOREIGN KEY (policy_id, tenant_id)          REFERENCES policies (id, tenant_id),
    -- SET NULL, not RESTRICT: DELETE /risks/{id} hard-deletes (registers.py), so a RESTRICT
    -- here would be the dormant-FK trap P4-S7/S8 both hit — the first task ever linked to a
    -- risk would turn that risk's delete into a 500. A task can outlive the risk that raised
    -- it; same reasoning as tasks_document_fk below and obligations_clause_fk.
    FOREIGN KEY (risk_id, tenant_id)            REFERENCES risks (id, tenant_id) ON DELETE SET NULL (risk_id),
    FOREIGN KEY (assignee_person_id, tenant_id) REFERENCES people   (id, tenant_id) ON DELETE RESTRICT
);

-- One occurrence of a recurring task.
-- NOTE: deliberately NOT UNIQUE (task_id, due_at). complete_run() derives the next
-- due date from TODAY, so completing a run early reproduces the same due_at; a unique
-- constraint would 500 on the normal 'finished it ahead of time' path. Revisit only if
-- the engine is changed to anchor cadence to the run's own due_at.
CREATE TABLE task_runs (
    id                     text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id              text NOT NULL REFERENCES tenants(id),
    task_id                text NOT NULL,
    due_at                 iso_ts NOT NULL,
    status                 text NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'done', 'overdue', 'skipped')),
    completed_at           iso_ts,
    completed_by_member_id text REFERENCES tenant_members(id),
    evidence_id            text,
    notes                  text,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (task_id, tenant_id)     REFERENCES tasks (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id)
);


-- =====================================================================================
-- L6 · AUDIT RESPONSE — templates, assessments, the crosswalk, findings (ours; the moat)
-- =====================================================================================

CREATE TABLE templates (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id),
    bank_name      text NOT NULL,
    title          text NOT NULL,
    version_label  text,
    source_file_id text,
    status         text NOT NULL DEFAULT 'importing'
                       CHECK (status IN ('importing', 'active', 'archived')),
    notes          text,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_file_id, tenant_id) REFERENCES files (id, tenant_id)
);

CREATE TABLE template_sections (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id),
    template_id text NOT NULL,
    parent_id   text,
    title       text NOT NULL,
    sort_order  integer NOT NULL DEFAULT 0,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (template_id, tenant_id) REFERENCES templates (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id, tenant_id)   REFERENCES template_sections (id, tenant_id)
);

-- The bank's point. `number` is kept verbatim — duplicates exist (KSL #89), so it is NOT unique.
CREATE TABLE questions (
    id                 text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id          text NOT NULL REFERENCES tenants(id),
    template_id        text NOT NULL,
    section_id         text,
    number             text,
    text               text NOT NULL,
    rationale          text,
    testing_procedure  text,
    evidence_mandatory integer NOT NULL DEFAULT 0 CHECK (evidence_mandatory IN (0, 1)),
    classification     text,
    response_scale     text,                        -- JSON, e.g. ["yes","no","na"]
    sort_order         integer NOT NULL DEFAULT 0,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (template_id, tenant_id) REFERENCES templates (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (section_id, tenant_id)  REFERENCES template_sections (id, tenant_id)
);

-- ***THE CROSSWALK*** — bank question -> our control. Probo has no equivalent.
CREATE TABLE question_control_map (
    id                     text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id              text NOT NULL REFERENCES tenants(id),
    question_id            text NOT NULL,
    control_id             text NOT NULL,
    confidence             real,
    status                 text NOT NULL DEFAULT 'suggested'
                               CHECK (status IN ('suggested', 'confirmed', 'rejected')),
    confirmed_by_member_id text REFERENCES tenant_members(id),
    created_at             iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (question_id, control_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (question_id, tenant_id) REFERENCES questions (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (control_id, tenant_id)  REFERENCES controls  (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE scoring_configs (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id),
    template_id text NOT NULL UNIQUE,
    config      text NOT NULL,                      -- JSON: scale, LxI matrix, verdict bands (D7)
    updated_at  iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (template_id, tenant_id) REFERENCES templates (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE assessments (
    id                    text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id             text NOT NULL REFERENCES tenants(id),
    template_id           text NOT NULL,
    title                 text NOT NULL,
    bank_name             text,
    period_start          iso_ts,
    period_end            iso_ts,
    status                text NOT NULL DEFAULT 'draft'
                              CHECK (status IN ('draft', 'in_progress', 'submitted',
                                                'in_review', 'verdict_issued', 'closed')),
    predicted_verdict     text,
    verdict               text,
    verdict_notes         text,
    vendor_spoc_member_id text REFERENCES tenant_members(id),
    bank_spoc_name        text,
    bank_spoc_email       text,
    created_by_member_id  text REFERENCES tenant_members(id),
    created_at            iso_ts NOT NULL DEFAULT now_iso(),
    closed_at             iso_ts,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (template_id, tenant_id) REFERENCES templates (id, tenant_id)
);

-- D2. Auditors (PwC/Deloitte) are per-assessment guests, expiring and revocable.
-- The old unconditional UNIQUE (assessment_id, user_id) is REPLACED by a partial unique
-- index below, so a revoked auditor can be re-invited (assessments.py:537 relies on the
-- "existing or new" path). Guests are users with NO people row: they are not in our org chart.
CREATE TABLE assessment_guests (
    id                   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id            text NOT NULL REFERENCES tenants(id),
    assessment_id        text NOT NULL,
    user_id              text NOT NULL REFERENCES users(id),
    role                 text NOT NULL DEFAULT 'auditor' CHECK (role IN ('auditor', 'observer')),
    firm                 text,                          -- "PwC" — which firm audited us
    invited_by_member_id text REFERENCES tenant_members(id),
    invited_at           iso_ts NOT NULL DEFAULT now_iso(),
    expires_at           iso_ts,
    revoked_at           iso_ts,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE responses (
    id                        text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id                 text NOT NULL REFERENCES tenants(id),
    assessment_id             text NOT NULL,
    question_id               text NOT NULL,
    response_value            text CHECK (response_value IN ('yes', 'partial', 'no', 'na')),
    comment                   text,
    na_justification          text,
    workflow_status           text NOT NULL DEFAULT 'open'
                                  CHECK (workflow_status IN ('open', 'answered', 'ask_pending',
                                                             'actioned', 'validated', 'final')),
    final_status              text CHECK (final_status IN ('compliant', 'partially_compliant',
                                                           'non_compliant', 'not_applicable',
                                                           'clarification')),
    prefilled_from_control_id text,
    updated_by_user_id        text REFERENCES users(id),
    updated_at                iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (assessment_id, question_id),
    UNIQUE (id, tenant_id),
    UNIQUE (id, assessment_id),                     -- lets finding links prove the response
    FOREIGN KEY (assessment_id, tenant_id)             REFERENCES assessments (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (question_id, tenant_id)               REFERENCES questions   (id, tenant_id),
    FOREIGN KEY (prefilled_from_control_id, tenant_id) REFERENCES controls    (id, tenant_id)
);

-- Append-only answer history: what we told ICICI in 2025 vs 2026.
CREATE TABLE response_revisions (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id),
    response_id    text NOT NULL,
    rev_no         integer NOT NULL CHECK (rev_no >= 1),
    response_value text,
    comment        text,
    author_user_id text REFERENCES users(id),
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (response_id, rev_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE response_evidence (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    evidence_id text NOT NULL,
    PRIMARY KEY (response_id, evidence_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence  (id, tenant_id) ON DELETE CASCADE
);

-- P7-S1: an audit point can also point at a policy/register, an incident, or a physical/
-- virtual asset — not just evidence. Three tables mirroring response_evidence's own shape
-- exactly, rather than one polymorphic union (risk_links, above, shows that pattern exists
-- in this schema): response_evidence is the LOCAL precedent for this exact relationship and
-- is already fully wired end to end, so matching it is less schema and less risk than a new
-- pattern. All three attachments are optional, per the request.
CREATE TABLE response_documents (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    document_id text NOT NULL,
    PRIMARY KEY (response_id, document_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE response_incidents (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    incident_id text NOT NULL,
    PRIMARY KEY (response_id, incident_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (incident_id, tenant_id) REFERENCES incidents (id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE response_assets (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    asset_id    text NOT NULL,
    PRIMARY KEY (response_id, asset_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id)    REFERENCES assets    (id, tenant_id) ON DELETE CASCADE
);

-- The KSL ask -> action -> validation rounds. author_user_id: auditor guests post here.
CREATE TABLE review_messages (
    id             text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id      text NOT NULL REFERENCES tenants(id),
    assessment_id  text NOT NULL,
    response_id    text,                            -- NULL = assessment-level remark
    author_user_id text NOT NULL REFERENCES users(id),
    author_kind    text NOT NULL CHECK (author_kind IN ('member', 'auditor', 'system')),
    kind           text NOT NULL CHECK (kind IN ('remark', 'ask', 'action', 'validation')),
    body           text NOT NULL,
    created_at     iso_ts NOT NULL DEFAULT now_iso(),
    resolved_at    iso_ts,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (response_id, tenant_id)   REFERENCES responses   (id, tenant_id) ON DELETE CASCADE
);

-- R9: ORG-LEVEL CAPA. One real-world nonconformity = ONE row, however many banks raise it.
-- risk_score stays a PLAIN column (NOT generated): assessments.py:469 passes it explicitly,
-- and a GENERATED column makes that INSERT fail with 428C9 (the C2 fix).
-- There is deliberately NO "closed needs root_cause" CHECK: FindingPatch cannot supply
-- root_cause, so the constraint would fail 100% of closes (the C3 fix). Enforce in the app
-- once FindingPatch carries it.
-- owner_member_id is KEPT (not renamed to owner_person_id): FindingPatch writes it. The new
-- owner_person_id is additive, for when findings get a real human owner at M10.
CREATE TABLE findings (
    id                  text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           text NOT NULL REFERENCES tenants(id),
    reference           text,
    title               text NOT NULL,
    description         text,
    recommendation      text,
    kind                text CHECK (kind IN ('MINOR_NONCONFORMITY', 'MAJOR_NONCONFORMITY',
                                             'OBSERVATION', 'EXCEPTION')),
    likelihood          integer CHECK (likelihood BETWEEN 1 AND 3),   -- AnnexC 3x3, preserved
    impact              integer CHECK (impact BETWEEN 1 AND 3),
    risk_score          integer,                    -- app-computed (api/scoring.py) — NOT generated
    risk_rating         text CHECK (risk_rating IN ('low', 'medium', 'high', 'clarification')),
    root_cause          text,
    corrective_action   text,
    effectiveness_check text,
    effectiveness_checked_at iso_ts,
    risk_id             text,
    control_id          text,
    status              text NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'remediation', 'closed', 'accepted', 'false_positive')),
    owner_member_id     text REFERENCES tenant_members(id),
    owner_person_id     text,
    due_at              iso_ts,
    closed_at           iso_ts,
    created_at          iso_ts NOT NULL DEFAULT now_iso(),
    updated_at          iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, reference),
    FOREIGN KEY (owner_person_id, tenant_id) REFERENCES people   (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (risk_id, tenant_id)         REFERENCES risks    (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (control_id, tenant_id)      REFERENCES controls (id, tenant_id) ON DELETE RESTRICT
);

-- WHERE a finding surfaced. response_id lives HERE, not on findings: "raised in audit A
-- against question Q by U" is a property of the (finding, assessment) PAIRING. The composite
-- FK (response_id, assessment_id) makes citing a KSL response inside an ICICI link impossible.
CREATE TABLE finding_assessments (
    id                text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id         text NOT NULL REFERENCES tenants(id),
    finding_id        text NOT NULL,
    assessment_id     text NOT NULL,
    response_id       text,
    raised_by_user_id text REFERENCES users(id),    -- member OR auditor guest
    raised_at         iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (finding_id, assessment_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (finding_id, tenant_id)       REFERENCES findings    (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (assessment_id, tenant_id)    REFERENCES assessments (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (response_id, assessment_id)  REFERENCES responses   (id, assessment_id) ON DELETE SET NULL (response_id)
);


-- =====================================================================================
-- L7 · TRUST PORTAL & PLATFORM
-- =====================================================================================

-- M15. The public compliance portal. The NDA template is just a document.
CREATE TABLE trust_center (
    id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       text NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    slug            text NOT NULL UNIQUE,
    headline        text,
    intro_markdown  text,
    is_published    boolean NOT NULL DEFAULT false,
    nda_document_id text,
    created_at      iso_ts NOT NULL DEFAULT now_iso(),
    updated_at      iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id)
);
-- No trust_center_ndas table: an NDA signature is an electronic_signatures row reached by
-- the SAME magic-link engine as employee attestation. D-SIGN's "no identity required"
-- constraint is exactly what lets one engine serve internal AND external signers.

-- A bank's request for one PRIVATE document: REQUESTED -> GRANTED -> (REVOKED).
CREATE TABLE trust_center_document_access (
    id                   text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id            text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    trust_center_id      text NOT NULL,
    document_id          text NOT NULL,
    requester_email      email_addr NOT NULL,
    requester_name       text,
    requester_company    text,
    status               text NOT NULL DEFAULT 'REQUESTED'
                             CHECK (status IN ('REQUESTED', 'GRANTED', 'REJECTED', 'REVOKED')),
    nda_signature_id     text,                      -- the gate: no signature, no GRANT
    watermark_email      text,                      -- stamped per page for leak attribution
    granted_at           iso_ts,
    granted_by_person_id text,
    expires_at           iso_ts,
    rejected_reason      text,
    revoked_at           iso_ts,
    download_count       integer NOT NULL DEFAULT 0,
    last_downloaded_at   iso_ts,
    created_at           iso_ts NOT NULL DEFAULT now_iso(),
    updated_at           iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (id, tenant_id),
    CONSTRAINT tcda_grant_needs_nda CHECK (status <> 'GRANTED'
                                           OR (nda_signature_id IS NOT NULL AND granted_at IS NOT NULL)),
    FOREIGN KEY (trust_center_id, tenant_id)      REFERENCES trust_center (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, tenant_id)          REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (nda_signature_id, tenant_id)     REFERENCES electronic_signatures (id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (granted_by_person_id, tenant_id) REFERENCES people (id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE notifications (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        text NOT NULL,
    title       text NOT NULL,
    body        text,
    entity_type text,
    entity_id   text,
    read_at     iso_ts,
    created_at  iso_ts NOT NULL DEFAULT now_iso()
);

-- F8/M16. TAMPER-EVIDENT audit log: per-tenant HASH CHAIN, sealed by trigger, append-only.
-- Also the e-signature event trail (no separate electronic_signature_events table — this one
-- is the trail, and unlike Probo's it is tamper-EVIDENT rather than tamper-discouraged).
-- `detail` stays TEXT (app already json.dumps at api/activity.py): raw text is deterministic
-- to hash, and avoids the jsonb key-ordering question entirely.
CREATE TABLE activity_log (
    id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       text NOT NULL REFERENCES tenants(id),
    seq             bigint NOT NULL,                -- assigned by the seal trigger
    actor_kind      text NOT NULL DEFAULT 'system'
                        CHECK (actor_kind IN ('member', 'auditor', 'person_token', 'external',
                                              'system', 'platform')),
    actor_user_id   text REFERENCES users(id),
    actor_person_id text,                           -- magic-link actor with NO login (D-SIGN)
    actor_label     text,                           -- an external signer's email
    actor_ip        inet,
    action          text NOT NULL,                  -- response.updated | evidence.uploaded | document.signed
    entity_type     text,
    entity_id       text,
    detail          text,                           -- JSON (app-serialised)
    prev_hash       text,                           -- NULL only for a tenant's genesis row
    entry_hash      text NOT NULL,
    created_at      iso_ts NOT NULL DEFAULT now_iso(),
    UNIQUE (tenant_id, seq),
    FOREIGN KEY (actor_person_id, tenant_id) REFERENCES people (id, tenant_id)
);


-- =====================================================================================
-- CROSS-LAYER FKs — added last (dependency cycles / forward references)
-- =====================================================================================
-- P4: tenants is declared before users, so its owner FK lands here.
ALTER TABLE tenants ADD CONSTRAINT tenants_super_admin_fk
    FOREIGN KEY (super_admin_user_id) REFERENCES users (id) ON DELETE SET NULL;

-- P6: an organisation's logo must be a file that BELONGS to it. The composite reference onto
-- files (id, tenant_id) is what enforces that — a plain FK on id alone would happily let one
-- tenant point at another tenant's upload, which is a cross-tenant read through an <img>.
-- SET NULL on delete: losing the logo is a cosmetic problem, blocking the file's deletion is
-- an operational one.
-- The column list on SET NULL is load-bearing: `id` is this table's primary key, so a bare
-- ON DELETE SET NULL would try to null it and fail. Same fix as tenant_members_role_fk below.
ALTER TABLE tenants ADD CONSTRAINT tenants_logo_fk
    FOREIGN KEY (logo_file_id, id) REFERENCES files (id, tenant_id)
    ON DELETE SET NULL (logo_file_id);

-- P4-S2: a member's role must belong to the SAME organisation as the membership.
ALTER TABLE tenant_members ADD CONSTRAINT tenant_members_role_fk
    FOREIGN KEY (role_id, tenant_id) REFERENCES roles (id, tenant_id) ON DELETE SET NULL (role_id);

ALTER TABLE documents ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (current_published_version_id, tenant_id)
    REFERENCES document_versions (id, tenant_id) ON DELETE SET NULL (current_published_version_id);

ALTER TABLE document_approval_decisions ADD CONSTRAINT dad_esig_fk
    FOREIGN KEY (e_signature_id, tenant_id) REFERENCES electronic_signatures (id, tenant_id) ON DELETE RESTRICT;

ALTER TABLE signing_tokens ADD CONSTRAINT st_target_signature_fk
    FOREIGN KEY (document_signature_id, tenant_id)
    REFERENCES document_signatures (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE signing_tokens ADD CONSTRAINT st_target_approval_fk
    FOREIGN KEY (document_approval_decision_id, tenant_id)
    REFERENCES document_approval_decisions (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE signing_tokens ADD CONSTRAINT st_target_trust_fk
    FOREIGN KEY (trust_access_id, tenant_id)
    REFERENCES trust_center_document_access (id, tenant_id) ON DELETE CASCADE;

ALTER TABLE statements_of_applicability ADD CONSTRAINT soa_document_fk
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE RESTRICT;

ALTER TABLE risk_links ADD CONSTRAINT rl_control_fk
    FOREIGN KEY (control_id, tenant_id)     REFERENCES controls      (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE risk_links ADD CONSTRAINT rl_document_fk
    FOREIGN KEY (document_id, tenant_id)    REFERENCES documents     (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE risk_links ADD CONSTRAINT rl_obligation_fk
    FOREIGN KEY (obligation_id, tenant_id)  REFERENCES obligations   (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE risk_links ADD CONSTRAINT rl_asset_fk
    FOREIGN KEY (asset_id, tenant_id)       REFERENCES assets        (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE risk_links ADD CONSTRAINT rl_third_party_fk
    FOREIGN KEY (third_party_id, tenant_id) REFERENCES third_parties (id, tenant_id) ON DELETE CASCADE;
ALTER TABLE risk_links ADD CONSTRAINT rl_incident_fk
    FOREIGN KEY (incident_id, tenant_id)    REFERENCES incidents     (id, tenant_id) ON DELETE CASCADE;

ALTER TABLE assets ADD CONSTRAINT assets_vendor_fk
    FOREIGN KEY (vendor_third_party_id, tenant_id) REFERENCES third_parties (id, tenant_id) ON DELETE RESTRICT;
ALTER TABLE obligations ADD CONSTRAINT obligations_clause_fk
    FOREIGN KEY (clause_id, tenant_id) REFERENCES framework_clauses (id, tenant_id) ON DELETE SET NULL (clause_id);
ALTER TABLE training_assignments ADD CONSTRAINT ta_evidence_fk
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id) ON DELETE RESTRICT;
ALTER TABLE third_party_assessments ADD CONSTRAINT tpa_evidence_fk
    FOREIGN KEY (evidence_id, tenant_id) REFERENCES evidence (id, tenant_id) ON DELETE RESTRICT;
ALTER TABLE access_review_campaigns ADD CONSTRAINT arc_document_fk
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE RESTRICT;
ALTER TABLE trust_center ADD CONSTRAINT tc_nda_document_fk
    FOREIGN KEY (nda_document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE RESTRICT;
ALTER TABLE tasks ADD CONSTRAINT tasks_document_fk
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE SET NULL (document_id);
-- P4-S9: assessments is declared after tasks, so this FK cannot be inline. SET NULL for the
-- same reason as tasks_document_fk above — no assessment-delete endpoint exists today, but
-- one might, and a task should survive the audit that raised it.
ALTER TABLE tasks ADD CONSTRAINT tasks_assessment_fk
    FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments (id, tenant_id) ON DELETE SET NULL (assessment_id);


-- =====================================================================================
-- INVARIANTS THE DATABASE ENFORCES (not app convention)
-- =====================================================================================

-- P4: exactly one DRAFT per document, ever.
CREATE UNIQUE INDEX uq_document_single_draft ON document_versions (document_id) WHERE status = 'DRAFT';
-- One open approval round per version.
CREATE UNIQUE INDEX uq_approval_single_open  ON document_approvals (document_version_id) WHERE status = 'PENDING';
-- One live GENERATED document per generator per tenant (no two Risk Lists).
CREATE UNIQUE INDEX uq_one_generator_doc ON documents (tenant_id, generator_key)
    WHERE generator_key IS NOT NULL AND status = 'ACTIVE';
-- One live guest grant per auditor per assessment — PARTIAL, so a revoked auditor can be
-- re-invited. This REPLACES the ported unconditional UNIQUE (assessment_id, user_id).
CREATE UNIQUE INDEX uq_guest_live ON assessment_guests (assessment_id, user_id) WHERE revoked_at IS NULL;
-- One trust-portal access grant per (document, requester) that is still live.
CREATE UNIQUE INDEX uq_trust_access_live ON trust_center_document_access (document_id, requester_email)
    WHERE status IN ('REQUESTED', 'GRANTED');

-- ---- tenant_id inheritance: NOT NULL tenant_id at ZERO app cost -----------------------
-- Every one of these child tables is INSERTed by the live API WITHOUT a tenant_id
-- (verified: assessments.py:196/204/433, tasks.py:61, tasks_engine.py:51/104,
-- templates.py import path). Without these triggers, composite-FK tenancy would break
-- every one of those writes with a NOT NULL violation.
CREATE TRIGGER trg_responses_tenant          BEFORE INSERT ON responses
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('assessments', 'assessment_id');
CREATE TRIGGER trg_response_revisions_tenant BEFORE INSERT ON response_revisions
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
CREATE TRIGGER trg_response_evidence_tenant  BEFORE INSERT ON response_evidence
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
-- P7-S1: response_documents/incidents/assets INSERT the same way link_evidence does
-- (api/routers/assessments.py) — no tenant_id in the values() call — so they need the
-- same trigger response_evidence has, or every link_document/incident/asset call 500s on
-- the NOT NULL violation. Caught by pytest, not by inspection: this project has no
-- Alembic, so a schema.sql edit without the matching trigger looks correct on read.
CREATE TRIGGER trg_response_documents_tenant BEFORE INSERT ON response_documents
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
CREATE TRIGGER trg_response_incidents_tenant BEFORE INSERT ON response_incidents
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
CREATE TRIGGER trg_response_assets_tenant    BEFORE INSERT ON response_assets
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
CREATE TRIGGER trg_review_messages_tenant    BEFORE INSERT ON review_messages
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('assessments', 'assessment_id');
CREATE TRIGGER trg_finding_assessments_tenant BEFORE INSERT ON finding_assessments
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('assessments', 'assessment_id');
CREATE TRIGGER trg_task_runs_tenant          BEFORE INSERT ON task_runs
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('tasks', 'task_id');
CREATE TRIGGER trg_questions_tenant          BEFORE INSERT ON questions
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('templates', 'template_id');
CREATE TRIGGER trg_template_sections_tenant  BEFORE INSERT ON template_sections
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('templates', 'template_id');
CREATE TRIGGER trg_qcm_tenant                BEFORE INSERT ON question_control_map
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('questions', 'question_id');
CREATE TRIGGER trg_scoring_configs_tenant    BEFORE INSERT ON scoring_configs
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('templates', 'template_id');
CREATE TRIGGER trg_assessment_guests_tenant  BEFORE INSERT ON assessment_guests
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('assessments', 'assessment_id');
CREATE TRIGGER trg_evidence_controls_tenant  BEFORE INSERT ON evidence_controls
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('evidence', 'evidence_id');
CREATE TRIGGER trg_incident_events_tenant    BEFORE INSERT ON incident_events
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('incidents', 'incident_id');
CREATE TRIGGER trg_incident_evidence_tenant  BEFORE INSERT ON incident_evidence
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('incidents', 'incident_id');
CREATE TRIGGER trg_risk_evidence_tenant      BEFORE INSERT ON risk_evidence
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('risks', 'risk_id');
CREATE TRIGGER trg_cer_tenant                BEFORE INSERT ON control_evidence_requirements
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('controls', 'control_id');
CREATE TRIGGER trg_policy_versions_tenant    BEFORE INSERT ON policy_versions
    FOR EACH ROW EXECUTE FUNCTION inherit_tenant('policies', 'policy_id');
-- NOTE: `findings` gets NO inheritance trigger. It is now org-level (R9) and has no single
-- parent to inherit from — so create_finding() MUST pass tenant_id explicitly. That handler
-- is being rewritten anyway (the C1 fix); adding tenant_id is one line of that same change.

-- ---- append-only enforcement ---------------------------------------------------------
-- UPDATE denied on all four. DELETE denied only on activity_log, whose tenant FK is
-- RESTRICT and which nothing cascades into. The other three sit under ON DELETE CASCADE
-- parents; denying their DELETE would make assessments/campaigns permanently undeletable
-- with an opaque error (the M6 fix).
CREATE TRIGGER trg_activity_immutable BEFORE UPDATE OR DELETE ON activity_log
    FOR EACH ROW EXECUTE FUNCTION deny_change();
CREATE TRIGGER trg_rev_immutable      BEFORE UPDATE ON response_revisions
    FOR EACH ROW EXECUTE FUNCTION deny_update();
CREATE TRIGGER trg_esig_immutable     BEFORE UPDATE ON electronic_signatures
    FOR EACH ROW EXECUTE FUNCTION deny_update();
CREATE TRIGGER trg_ard_immutable      BEFORE UPDATE ON access_review_decisions
    FOR EACH ROW EXECUTE FUNCTION deny_update();
-- P4-S7. Same reasoning: UPDATE denied (the timeline is tamper-evident), DELETE allowed
-- because incident_events cascades from incidents.
CREATE TRIGGER trg_incident_events_immutable BEFORE UPDATE ON incident_events
    FOR EACH ROW EXECUTE FUNCTION deny_update();

-- ---- the hash chain ------------------------------------------------------------------
-- Hashes the FULL actor tuple. For a login-less magic-link signer both actor_user_id and
-- actor_person_id are NULL and actor_label (the external signer's email) is the ONLY
-- identity in the row — leaving it unsealed would let an external NDA signer's identity and
-- IP be rewritten while the chain still verified, exactly where D-SIGN needs it to hold
-- (the C5 fix).
CREATE FUNCTION activity_entry_hash(
    p_prev text, p_tenant text, p_seq bigint, p_actor_kind text, p_user text, p_person text,
    p_label text, p_ip inet, p_action text, p_etype text, p_eid text, p_detail text, p_created text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT sha256_hex(concat_ws(U&'\0001',
        coalesce(p_prev, ''), p_tenant, p_seq::text, p_actor_kind, coalesce(p_user, ''),
        coalesce(p_person, ''), coalesce(p_label, ''), coalesce(host(p_ip), ''), p_action,
        coalesce(p_etype, ''), coalesce(p_eid, ''), coalesce(p_detail, ''), p_created)) $$;

CREATE FUNCTION activity_log_seal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prev_seq bigint; prev_h text;
BEGIN
    -- a chain cannot be appended concurrently
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id, 0));
    SELECT seq, entry_hash INTO prev_seq, prev_h
      FROM activity_log WHERE tenant_id = NEW.tenant_id ORDER BY seq DESC LIMIT 1;
    NEW.seq       := coalesce(prev_seq, 0) + 1;
    NEW.prev_hash := prev_h;
    NEW.entry_hash := activity_entry_hash(NEW.prev_hash, NEW.tenant_id, NEW.seq, NEW.actor_kind,
                                          NEW.actor_user_id, NEW.actor_person_id, NEW.actor_label,
                                          NEW.actor_ip, NEW.action, NEW.entity_type,
                                          NEW.entity_id, NEW.detail, NEW.created_at);
    RETURN NEW;
END $$;
CREATE TRIGGER trg_activity_seal BEFORE INSERT ON activity_log
    FOR EACH ROW EXECUTE FUNCTION activity_log_seal();

-- Nightly verification; ship the (empty) output to the auditor.
CREATE VIEW v_activity_chain_breaks AS
SELECT a.tenant_id, a.seq, 'entry_hash mismatch' AS problem
  FROM activity_log a
 WHERE a.entry_hash <> activity_entry_hash(a.prev_hash, a.tenant_id, a.seq, a.actor_kind,
        a.actor_user_id, a.actor_person_id, a.actor_label, a.actor_ip, a.action,
        a.entity_type, a.entity_id, a.detail, a.created_at)
UNION ALL
SELECT a.tenant_id, a.seq, 'prev_hash mismatch'
  FROM activity_log a
 WHERE a.prev_hash IS DISTINCT FROM
       (SELECT p.entry_hash FROM activity_log p WHERE p.tenant_id = a.tenant_id AND p.seq = a.seq - 1);

-- ---- D-APPROVAL: M-of-N is enforced by the DB, not by convention ----------------------
-- A version cannot reach PUBLISHED unless its approval round collected >= threshold_required
-- APPROVED decisions. This also blocks the "minor version publish silently bypasses
-- approval + re-attestation" hole D-APPROVAL calls out (the M3 fix).
CREATE FUNCTION assert_publish_approved() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE need integer; got integer;
BEGIN
    IF NEW.status = 'PUBLISHED' AND OLD.status IS DISTINCT FROM 'PUBLISHED' THEN
        SELECT a.threshold_required,
               (SELECT count(*) FROM document_approval_decisions d
                 WHERE d.approval_id = a.id AND d.state = 'APPROVED')
          INTO need, got
          FROM document_approvals a
         WHERE a.document_version_id = NEW.id AND a.status <> 'CANCELLED'
         -- id DESC: deterministic tiebreaker so this matches the app's _latest_approval
         -- exactly. opened_at is second-resolution; without a tiebreaker guard and trigger
         -- could pick different tied rows and disagree (a spurious 500 / block).
         ORDER BY a.opened_at DESC, a.id DESC LIMIT 1;
        IF need IS NULL THEN
            RAISE EXCEPTION 'version % cannot be published: no approval round', NEW.id;
        END IF;
        IF got < need THEN
            RAISE EXCEPTION 'version % cannot be published: % of % approvals', NEW.id, got, need;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_dv_publish_approved BEFORE UPDATE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION assert_publish_approved();

-- M-of-N sanity: threshold can never exceed the number of approvers invited. Deferred, so
-- it is checked at COMMIT — after the decision rows have been inserted alongside the round.
-- M3 FIX (was: looped every PENDING approval in the whole DB, so an unrelated tenant's
-- half-built round could fail your commit). Scope strictly to the row that fired.
CREATE FUNCTION assert_threshold_le_approvers() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM document_approval_decisions d WHERE d.approval_id = NEW.id;
    IF NEW.threshold_required > n THEN
        RAISE EXCEPTION 'approval %: threshold % exceeds % approvers', NEW.id, NEW.threshold_required, n;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_mofn_sane AFTER INSERT OR UPDATE ON document_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION assert_threshold_le_approvers();

-- M5 FIX: a PUBLISHED version's bytes are what people approved and signed. Freeze them.
-- Metadata (status→SUPERSEDED, published_at, file_id, updated_at) may still change; the
-- signed content / version number cannot, once the version has ever been published.
CREATE FUNCTION freeze_published_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- content_format is frozen too: content_sha256 covers `content` alone, so flipping the
    -- format on a published, attested version would change how the signed bytes render
    -- while leaving the hash — and therefore the signature — apparently intact.
    IF OLD.status IN ('PUBLISHED', 'SUPERSEDED')
       AND (NEW.content IS DISTINCT FROM OLD.content
            OR NEW.content_format IS DISTINCT FROM OLD.content_format
            OR NEW.major IS DISTINCT FROM OLD.major
            OR NEW.minor IS DISTINCT FROM OLD.minor) THEN
        RAISE EXCEPTION 'version %.% is published and immutable; its content cannot change',
            OLD.major, OLD.minor;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_dv_freeze BEFORE UPDATE ON document_versions
    FOR EACH ROW EXECUTE FUNCTION freeze_published_version();

-- ---- flag_count stays true to the flag rows ------------------------------------------
CREATE FUNCTION sync_flag_count() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE eid text;
BEGIN
    eid := coalesce(NEW.entry_id, OLD.entry_id);
    UPDATE access_review_entries e
       SET flag_count = (SELECT count(*) FROM access_review_entry_flags f WHERE f.entry_id = eid)
     WHERE e.id = eid;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_flag_count AFTER INSERT OR DELETE ON access_review_entry_flags
    FOR EACH ROW EXECUTE FUNCTION sync_flag_count();

-- ---- updated_at maintenance (NEW tables only) ----------------------------------------
-- Ported tables are excluded on purpose: the app already sets updated_at itself, and
-- tenants/templates have no updated_at column at all (a blanket trigger would raise on
-- every UPDATE). scoring_configs IS included — it has updated_at and was missed by draft B.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['people', 'trainings', 'training_assignments', 'risks', 'assets',
      'data_items', 'third_parties', 'third_party_assessments', 'obligations', 'incidents',
      'access_review_campaigns', 'access_review_entries', 'frameworks',
      'statements_of_applicability', 'documents', 'document_versions', 'findings',
      'trust_center', 'trust_center_document_access']
  LOOP
    EXECUTE format('CREATE TRIGGER trg_%s_updated BEFORE UPDATE ON %I
                    FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;


-- =====================================================================================
-- INDEXES — every one serves a named screen, queue or join.
-- ISO-8601 TEXT sorts chronologically, so these btrees answer date ranges exactly.
-- =====================================================================================

-- ---- ported access paths (kept 1:1 with db/schema.sql) --------------------------------
CREATE INDEX idx_members_tenant       ON tenant_members (tenant_id);
CREATE INDEX idx_members_user         ON tenant_members (user_id);
CREATE INDEX idx_files_tenant         ON files (tenant_id);
CREATE INDEX idx_domains_tenant       ON domains (tenant_id);
CREATE INDEX idx_controls_tenant      ON controls (tenant_id);
CREATE INDEX idx_controls_domain      ON controls (domain_id);
CREATE INDEX idx_policies_tenant      ON policies (tenant_id);
CREATE INDEX idx_policies_review      ON policies (next_review_at);
CREATE INDEX idx_polversions_policy   ON policy_versions (policy_id);
CREATE INDEX idx_evidence_tenant      ON evidence (tenant_id);
CREATE INDEX idx_templates_tenant     ON templates (tenant_id);
CREATE INDEX idx_sections_template    ON template_sections (template_id);
CREATE INDEX idx_assessments_tenant   ON assessments (tenant_id);
CREATE INDEX idx_guests_assessment    ON assessment_guests (assessment_id);
CREATE INDEX idx_guests_user          ON assessment_guests (user_id);
CREATE INDEX idx_responses_assessment ON responses (assessment_id);
CREATE INDEX idx_revisions_response   ON response_revisions (response_id);
CREATE INDEX idx_messages_assessment  ON review_messages (assessment_id);
CREATE INDEX idx_tasks_tenant         ON tasks (tenant_id);
CREATE INDEX idx_taskruns_task        ON task_runs (task_id);
CREATE INDEX idx_notifications_user   ON notifications (user_id, read_at);
CREATE INDEX idx_activity_tenant      ON activity_log (tenant_id, created_at);

-- ---- DASHBOARD QUEUES (partial: index only rows a queue can ever return) --------------
CREATE INDEX ix_evidence_expiring   ON evidence (tenant_id, valid_until)
    WHERE state = 'FULFILLED' AND valid_until IS NOT NULL;              -- ***D-MOAT***
CREATE INDEX ix_evidence_gaps       ON evidence (tenant_id, due_at)     WHERE state = 'REQUESTED';
CREATE INDEX ix_taskruns_due        ON task_runs (tenant_id, due_at)    WHERE status IN ('pending', 'overdue');
CREATE INDEX ix_tasks_due           ON tasks (tenant_id, next_due_at)   WHERE status = 'active';
CREATE INDEX ix_policies_due        ON policies (tenant_id, next_review_at) WHERE status = 'active';
CREATE INDEX ix_documents_review    ON documents (tenant_id, next_review_at)
    WHERE status = 'ACTIVE' AND next_review_at IS NOT NULL;
CREATE INDEX ix_messages_open_ask   ON review_messages (tenant_id, assessment_id)
    WHERE kind = 'ask' AND resolved_at IS NULL;                         -- open auditor asks
CREATE INDEX ix_responses_ask       ON responses (tenant_id, workflow_status)
    WHERE workflow_status = 'ask_pending';
CREATE INDEX ix_signatures_pending  ON document_signatures (tenant_id, due_at) WHERE state = 'REQUESTED';
CREATE INDEX ix_approvals_pending   ON document_approval_decisions (tenant_id, approver_person_id)
    WHERE state = 'PENDING';
CREATE INDEX ix_tpa_expiring        ON third_party_agreements (tenant_id, valid_until) WHERE valid_until IS NOT NULL;
CREATE INDEX ix_tpassess_expiring   ON third_party_assessments (tenant_id, expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX ix_obligations_review  ON obligations (tenant_id, next_review_date) WHERE next_review_date IS NOT NULL;
CREATE INDEX ix_risks_review        ON risks (tenant_id, next_review_at) WHERE status = 'OPEN';
CREATE INDEX ix_training_due        ON training_assignments (tenant_id, due_at) WHERE status IN ('ASSIGNED', 'OVERDUE');
CREATE INDEX ix_findings_open       ON findings (tenant_id, due_at) WHERE status IN ('open', 'remediation');
CREATE INDEX ix_are_pending         ON access_review_entries (tenant_id, campaign_id, flag_count DESC)
    WHERE decision = 'PENDING';
CREATE INDEX ix_notifications_unread ON notifications (user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX ix_trust_pending       ON trust_center_document_access (tenant_id, created_at) WHERE status = 'REQUESTED';
CREATE INDEX ix_tokens_live         ON signing_tokens (tenant_id, expires_at)
    WHERE consumed_at IS NULL AND revoked_at IS NULL;

-- ---- GENERATED-document drift probes (M11) -------------------------------------------
CREATE INDEX ix_risks_touched         ON risks (tenant_id, updated_at);
CREATE INDEX ix_assets_touched        ON assets (tenant_id, updated_at);
CREATE INDEX ix_data_items_touched    ON data_items (tenant_id, updated_at);
CREATE INDEX ix_third_parties_touched ON third_parties (tenant_id, updated_at);
CREATE INDEX ix_incidents_touched     ON incidents (tenant_id, updated_at);
CREATE INDEX ix_obligations_touched   ON obligations (tenant_id, updated_at);
CREATE INDEX ix_findings_touched      ON findings (tenant_id, updated_at);
CREATE INDEX ix_ta_touched            ON training_assignments (tenant_id, updated_at);

-- ---- THE CROSSWALK + navigation ------------------------------------------------------
CREATE INDEX idx_qcm_question     ON question_control_map (question_id);
CREATE INDEX idx_qcm_control      ON question_control_map (control_id);
CREATE INDEX ix_qcm_confirmed     ON question_control_map (control_id) WHERE status = 'confirmed';
CREATE INDEX idx_questions_template ON questions (template_id, sort_order);
CREATE INDEX ix_people_dept       ON people (tenant_id, department) WHERE state = 'ACTIVE';
CREATE INDEX ix_people_manager    ON people (tenant_id, manager_id);
CREATE INDEX ix_controls_domain   ON controls (tenant_id, domain_id) WHERE status = 'active';
CREATE INDEX ix_cer_control       ON control_evidence_requirements (control_id);
CREATE INDEX ix_evidence_requirement ON evidence (requirement_id) WHERE requirement_id IS NOT NULL;
CREATE INDEX idx_messages_response ON review_messages (response_id, created_at);
CREATE INDEX ix_dv_document       ON document_versions (document_id, major DESC, minor DESC);
CREATE INDEX ix_ec_control        ON evidence_controls (control_id);
CREATE INDEX ix_re_evidence       ON response_evidence (evidence_id);
CREATE INDEX ix_ccm_clause        ON control_clause_map (clause_id);
-- the PK covers the control_id leg; these index the reverse lookups
CREATE INDEX ix_cd_document       ON control_documents (document_id);
CREATE INDEX ix_com_obligation    ON control_obligation_map (obligation_id);
-- P4-S7. The timeline is always read incident-then-chronological; the two joins' PKs
-- already cover their first leg, so these index the reverse (evidence -> where used).
CREATE INDEX ix_incident_events_incident   ON incident_events   (incident_id, occurred_at);
CREATE INDEX ix_incident_evidence_evidence ON incident_evidence (evidence_id);
CREATE INDEX ix_risk_evidence_evidence     ON risk_evidence     (evidence_id);
CREATE INDEX ix_clauses_framework ON framework_clauses (framework_id, sort_order);
CREATE INDEX ix_risk_links_risk   ON risk_links (risk_id);
-- One link per (risk, target): backs the app's dedupe so a concurrent double-click can't
-- land two identical links (which would double the reverse-nav count). num_nonnulls=1 is
-- guaranteed by rl_exactly_one, so coalesce() resolves to the single target id.
CREATE UNIQUE INDEX uq_risk_link_target ON risk_links
    (risk_id, target_kind, coalesce(control_id, document_id, obligation_id,
                                    asset_id, third_party_id, incident_id));
CREATE INDEX ix_fa_assessment     ON finding_assessments (assessment_id);
CREATE INDEX ix_fa_finding        ON finding_assessments (finding_id);
CREATE INDEX ix_tp_parent         ON third_parties (tenant_id, parent_third_party_id)
    WHERE parent_third_party_id IS NOT NULL;                            -- the 4th-party chain
CREATE INDEX ix_ds_version        ON document_signatures (document_version_id);
CREATE INDEX ix_audiences_document ON document_audiences (document_id);
CREATE INDEX ix_activity_entity   ON activity_log (tenant_id, entity_type, entity_id);
CREATE INDEX ix_activity_recent   ON activity_log (tenant_id, created_at DESC);

-- ---- containment ----------------------------------------------------------------------
CREATE INDEX ix_assets_datatypes ON assets USING gin (data_types_stored);
CREATE INDEX ix_tp_certs         ON third_parties USING gin (certifications);
CREATE INDEX ix_tp_countries     ON third_parties USING gin (countries);
CREATE INDEX ix_are_roles        ON access_review_entries USING gin (roles);


-- =====================================================================================
-- VIEWS — the dashboard is SELECTs, not hand-written UNIONs in Python.
-- NOTE: api/database.py reflects with views=False, so t('...') CANNOT see these. They are
-- for raw-SQL reads (conn.execute(text(...))), exports and reporting — not Core queries.
-- =====================================================================================

-- Contract dates are authoritative over the stored flag.
CREATE VIEW v_people_effective_state AS
SELECT p.*,
       CASE WHEN p.state = 'INACTIVE' THEN 'INACTIVE'
            WHEN p.contract_end_date IS NOT NULL
             AND p.contract_end_date::date < current_date THEN 'INACTIVE'
            ELSE 'ACTIVE' END AS effective_state
  FROM people p;

-- D-MOAT. 60 days matches api/util.evidence_status(soon_days=60) — keep them in step.
CREATE VIEW v_evidence_freshness AS
SELECT e.*,
       (e.valid_until::date - current_date) AS days_to_expiry,
       (e.state = 'FULFILLED' AND e.valid_until IS NOT NULL
        AND e.valid_until::date < current_date) AS is_expired,
       (e.state = 'FULFILLED' AND e.valid_until IS NOT NULL
        AND e.valid_until::date BETWEEN current_date AND current_date + 60) AS is_expiring
  FROM evidence e;

-- ***THE GAP LIST*** (E1 + D-MOAT in ONE query): every control requirement with no FRESH
-- FULFILLED evidence. This is what kills the last-moment scramble.
-- Two fixes over the drafts (the M1 fix):
--   1. cadence_months is USED, not just selected. Effective expiry =
--      coalesce(valid_until, issued_at + cadence_months) — so an UNDATED artifact against a
--      12-month requirement still goes STALE. Previously it never could.
--   2. Ranking is by EFFECTIVE expiry, not `coalesce(valid_until,'infinity')`, which sorted
--      no-expiry evidence FIRST and let one legacy NULL-expiry upload mask the gap forever.
CREATE VIEW v_evidence_gaps AS
SELECT r.tenant_id, r.id AS requirement_id, r.control_id, c.code AS control_code,
       c.statement, r.evidence_type, r.cadence_months, r.is_mandatory, c.owner_person_id,
       CASE WHEN e.id IS NULL THEN 'MISSING' ELSE 'STALE' END AS gap_kind,
       e.id AS stale_evidence_id, e.effective_until
  FROM control_evidence_requirements r
  JOIN controls c ON c.id = r.control_id
                 AND c.status = 'active' AND c.applicability = 'applicable'
  LEFT JOIN LATERAL (
       SELECT ev.id,
              coalesce(ev.valid_until::date,
                       CASE WHEN ev.issued_at IS NOT NULL AND r.cadence_months IS NOT NULL
                            THEN (ev.issued_at::date + (r.cadence_months || ' months')::interval)::date
                       END) AS effective_until
         FROM evidence ev
        WHERE ev.requirement_id = r.id
          AND ev.state = 'FULFILLED'
        ORDER BY coalesce(ev.valid_until::date,
                          CASE WHEN ev.issued_at IS NOT NULL AND r.cadence_months IS NOT NULL
                               THEN (ev.issued_at::date + (r.cadence_months || ' months')::interval)::date
                          END,
                          '-infinity'::date) DESC          -- undated ranks LAST, not first
        LIMIT 1) e ON true
 WHERE e.id IS NULL
    OR e.effective_until IS NULL                            -- undated + no cadence = unprovable
    OR e.effective_until < current_date;

-- D-AUDIENCE. Who SHOULD attest a published version, resolved from the audience rules —
-- computed live from people, so someone hired AFTER publish appears immediately.
CREATE VIEW v_document_expected_signers AS
SELECT DISTINCT dv.id AS document_version_id, dv.tenant_id, p.id AS person_id
  FROM document_versions dv
  JOIN documents d          ON d.id = dv.document_id
  JOIN document_audiences a ON a.document_id = d.id
  JOIN v_people_effective_state p ON p.tenant_id = d.tenant_id
   AND p.effective_state = 'ACTIVE'                         -- leavers never hold coverage down
   AND (a.rule = 'ALL_EMPLOYEES'
     OR (a.rule = 'DEPARTMENT' AND p.department = a.value)
     OR (a.rule = 'EXPLICIT'   AND p.id = a.person_id))
 WHERE dv.status = 'PUBLISHED';

-- P9 coverage %: "87% of staff have signed v3.0" — the number we show a bank.
-- Computed against the LIVE audience, not against the signature rows materialised at
-- publish: a person hired after publish has no signature row, and counting only those rows
-- reports 100% while they have never attested (the M4 fix).
-- EXEMPT people leave BOTH sides of the ratio (an exemption excuses a person, it must not
-- drag the % down): `expected` counts audience members who are not EXEMPT for this version,
-- `signed` counts the SIGNED. Join is unfiltered so the FILTERs can see the EXEMPT state.
CREATE VIEW v_document_coverage AS
SELECT dv.tenant_id, dv.document_id, dv.id AS version_id, dv.version_label,
       count(*) FILTER (WHERE s.state IS DISTINCT FROM 'EXEMPT')          AS expected,
       count(*) FILTER (WHERE s.state = 'SIGNED')                         AS signed,
       count(*) FILTER (WHERE s.state IS DISTINCT FROM 'EXEMPT'
                          AND s.state IS DISTINCT FROM 'SIGNED')          AS outstanding,
       round(100.0 * count(*) FILTER (WHERE s.state = 'SIGNED')
             / nullif(count(*) FILTER (WHERE s.state IS DISTINCT FROM 'EXEMPT'), 0), 1)
                                                                          AS coverage_pct
  FROM document_versions dv
  JOIN v_document_expected_signers x ON x.document_version_id = dv.id
  LEFT JOIN document_signatures s ON s.document_version_id = dv.id
                                 AND s.person_id = x.person_id
 WHERE dv.status = 'PUBLISHED'
 GROUP BY dv.tenant_id, dv.document_id, dv.id, dv.version_label;

-- Anyone in the audience with no signature row at all — the queue that re-materialises them.
CREATE VIEW v_document_missing_signature_rows AS
SELECT x.tenant_id, x.document_version_id, x.person_id
  FROM v_document_expected_signers x
  LEFT JOIN document_signatures s ON s.document_version_id = x.document_version_id
                                 AND s.person_id = x.person_id
 WHERE s.id IS NULL;

-- D-GENERATED drift: is a published register document stale vs its live register?
-- Uses row COUNT as well as max(updated_at): a max() probe alone cannot see a DELETION, so
-- removing a risk after publish left the document silently misrepresenting the register —
-- the exact failure D-GENERATED exists to prevent (the M2 fix). generator_key is also
-- CHECK-constrained on documents, so a typo can no longer fall through this CASE to NULL.
CREATE VIEW v_generated_document_drift AS
SELECT d.tenant_id, d.id AS document_id, d.title, d.generator_key,
       dv.version_label, dv.published_at,
       dv.source_row_count, src.row_count      AS current_row_count,
       dv.source_max_updated_at, src.last_change,
       (src.row_count IS DISTINCT FROM dv.source_row_count
        OR src.last_change IS DISTINCT FROM dv.source_max_updated_at) AS is_stale
  FROM documents d
  JOIN document_versions dv ON dv.id = d.current_published_version_id
  JOIN LATERAL (
       SELECT CASE d.generator_key
         WHEN 'risk_list'        THEN (SELECT count(*) FROM risks         WHERE tenant_id = d.tenant_id)
         WHEN 'asset_list'       THEN (SELECT count(*) FROM assets        WHERE tenant_id = d.tenant_id)
         WHEN 'data_list'        THEN (SELECT count(*) FROM data_items    WHERE tenant_id = d.tenant_id)
         WHEN 'third_party_list' THEN (SELECT count(*) FROM third_parties WHERE tenant_id = d.tenant_id)
         WHEN 'obligation_list'  THEN (SELECT count(*) FROM obligations   WHERE tenant_id = d.tenant_id)
         WHEN 'incident_list'    THEN (SELECT count(*) FROM incidents     WHERE tenant_id = d.tenant_id)
         WHEN 'finding_list'     THEN (SELECT count(*) FROM findings      WHERE tenant_id = d.tenant_id)
         WHEN 'training_records' THEN (SELECT count(*) FROM training_assignments WHERE tenant_id = d.tenant_id)
       END AS row_count,
       CASE d.generator_key
         WHEN 'risk_list'        THEN (SELECT max(updated_at) FROM risks         WHERE tenant_id = d.tenant_id)
         WHEN 'asset_list'       THEN (SELECT max(updated_at) FROM assets        WHERE tenant_id = d.tenant_id)
         WHEN 'data_list'        THEN (SELECT max(updated_at) FROM data_items    WHERE tenant_id = d.tenant_id)
         WHEN 'third_party_list' THEN (SELECT max(updated_at) FROM third_parties WHERE tenant_id = d.tenant_id)
         WHEN 'obligation_list'  THEN (SELECT max(updated_at) FROM obligations   WHERE tenant_id = d.tenant_id)
         WHEN 'incident_list'    THEN (SELECT max(updated_at) FROM incidents     WHERE tenant_id = d.tenant_id)
         WHEN 'finding_list'     THEN (SELECT max(updated_at) FROM findings      WHERE tenant_id = d.tenant_id)
         WHEN 'training_records' THEN (SELECT max(updated_at) FROM training_assignments WHERE tenant_id = d.tenant_id)
       END AS last_change) src ON true
 WHERE d.write_mode = 'GENERATED' AND d.status = 'ACTIVE';
-- Definitive check (use at publish time): re-render and compare against
-- document_versions.content_sha256. The view is the cheap always-on probe.

-- F5: the SoA's reason-for-inclusion is DERIVED from the graph, never stored.
CREATE VIEW v_soa_reason_for_inclusion AS
SELECT s.id AS soa_id, s.tenant_id, fc.id AS clause_id, fc.ref, fc.title,
       aps.applicable, aps.justification,
       EXISTS (SELECT 1 FROM control_clause_map m JOIN risk_links rl ON rl.control_id = m.control_id
                WHERE m.clause_id = fc.id AND rl.target_kind = 'CONTROL')      AS by_risk_assessment,
       EXISTS (SELECT 1 FROM obligations o WHERE o.clause_id = fc.id
                 AND o.type = 'LEGAL')                                         AS by_legal_requirement,
       EXISTS (SELECT 1 FROM obligations o WHERE o.clause_id = fc.id
                 AND o.type = 'CONTRACTUAL')                                   AS by_contractual_obligation,
       EXISTS (SELECT 1 FROM control_clause_map m JOIN controls c ON c.id = m.control_id
                WHERE m.clause_id = fc.id AND c.status = 'active')             AS by_business_requirement
  FROM statements_of_applicability s
  JOIN framework_clauses fc ON fc.framework_id = s.framework_id
  LEFT JOIN applicability_statements aps ON aps.soa_id = s.id AND aps.clause_id = fc.id;

-- Compat for the org-level findings move (R9): the shape the old
-- findings-per-assessment handlers expect. Raw SQL / exports only (see the views=False note).
CREATE VIEW v_assessment_findings AS
SELECT f.*, fa.assessment_id, fa.response_id, fa.raised_by_user_id, fa.raised_at
  FROM findings f JOIN finding_assessments fa ON fa.finding_id = f.id;

-- ***THE DASHBOARD.*** Everything anyone owes, one shape, one ORDER BY. Every branch hits a
-- partial index above. Not an API contract — swap for a matview if it ever slows down.
--   SELECT * FROM v_work_queue WHERE tenant_id = $1 AND due_on <= to_char(...)
CREATE VIEW v_work_queue AS
SELECT tenant_id, kind, entity_type, entity_id, title, due_on, owner_person_id FROM (
  SELECT tr.tenant_id, 'task_due' AS kind, 'task_run' AS entity_type, tr.id AS entity_id,
         t.title, tr.due_at AS due_on, t.assignee_person_id AS owner_person_id
    FROM task_runs tr JOIN tasks t ON t.id = tr.task_id WHERE tr.status IN ('pending', 'overdue')
  UNION ALL
  SELECT e.tenant_id, 'evidence_expiring', 'evidence', e.id, e.title, e.valid_until, NULL
    FROM evidence e WHERE e.state = 'FULFILLED' AND e.valid_until IS NOT NULL
  UNION ALL
  SELECT e.tenant_id, 'evidence_requested', 'evidence', e.id, e.title, e.due_at, NULL
    FROM evidence e WHERE e.state = 'REQUESTED'
  UNION ALL
  SELECT p.tenant_id, 'policy_review', 'policy', p.id, p.title, p.next_review_at, NULL
    FROM policies p WHERE p.status = 'active' AND p.next_review_at IS NOT NULL
  UNION ALL
  SELECT d.tenant_id, 'document_review', 'document', d.id, d.title, d.next_review_at, d.owner_person_id
    FROM documents d WHERE d.status = 'ACTIVE' AND d.next_review_at IS NOT NULL
  UNION ALL
  SELECT s.tenant_id, 'attestation_due', 'document_signature', s.id,
         dv.version_label, s.due_at, s.person_id
    FROM document_signatures s JOIN document_versions dv ON dv.id = s.document_version_id
   WHERE s.state = 'REQUESTED'
  UNION ALL
  SELECT dad.tenant_id, 'approval_due', 'document_approval_decision', dad.id,
         dv.version_label, dad.created_at, dad.approver_person_id
    FROM document_approval_decisions dad
    JOIN document_approvals da ON da.id = dad.approval_id
    JOIN document_versions dv  ON dv.id = da.document_version_id
   WHERE dad.state = 'PENDING' AND da.status = 'PENDING'
  UNION ALL
  SELECT o.tenant_id, 'obligation_review', 'obligation', o.id, o.requirement, o.next_review_date, o.owner_person_id
    FROM obligations o WHERE o.next_review_date IS NOT NULL
  UNION ALL
  SELECT r.tenant_id, 'risk_review', 'risk', r.id, r.title, r.next_review_at, r.owner_person_id
    FROM risks r WHERE r.status = 'OPEN' AND r.next_review_at IS NOT NULL
  UNION ALL
  SELECT a.tenant_id, 'vendor_assessment_expiring', 'third_party_assessment', a.id,
         tp.name, a.expires_at, tp.security_owner_person_id
    FROM third_party_assessments a JOIN third_parties tp ON tp.id = a.third_party_id
   WHERE a.expires_at IS NOT NULL
  UNION ALL
  SELECT g.tenant_id, 'agreement_expiring', 'third_party_agreement', g.id,
         tp.name || ' ' || g.kind, g.valid_until, tp.business_owner_person_id
    FROM third_party_agreements g JOIN third_parties tp ON tp.id = g.third_party_id
   WHERE g.valid_until IS NOT NULL
  UNION ALL
  SELECT ta.tenant_id, 'training_due', 'training_assignment', ta.id, tr.title, ta.due_at, ta.person_id
    FROM training_assignments ta JOIN trainings tr ON tr.id = ta.training_id
   WHERE ta.status IN ('ASSIGNED', 'OVERDUE')
  UNION ALL
  SELECT f.tenant_id, 'finding_due', 'finding', f.id, f.title, f.due_at, f.owner_person_id
    FROM findings f WHERE f.status IN ('open', 'remediation') AND f.due_at IS NOT NULL
  UNION ALL
  SELECT rm.tenant_id, 'auditor_ask', 'review_message', rm.id, left(rm.body, 120),
         rm.created_at, NULL
    FROM review_messages rm WHERE rm.kind = 'ask' AND rm.resolved_at IS NULL
) q;


-- =====================================================================================
-- ROW-LEVEL SECURITY — defence in depth against one forgotten WHERE tenant_id.
-- ENABLE (not FORCE) is deliberate: the API currently connects as the schema OWNER, and a
-- table owner is exempt from RLS unless FORCE is set. So this is INERT today and breaks
-- nothing, but is ready the moment the API switches to the app_rw role — which it MUST do
-- before M15 puts the trust portal on the public internet.
--
-- To activate:
--   1. CREATE ROLE app_rw LOGIN PASSWORD '...';  GRANT SELECT,INSERT,UPDATE,DELETE
--      ON ALL TABLES IN SCHEMA public TO app_rw;
--   2. point api/config.settings.database_url at app_rw
--   3. in api/database.get_conn(), per checkout:
--        conn.execute(text("SELECT set_config('audit_rail.tenant_id', :t, true)"), {"t": tid})
--      -- set_config takes a BIND PARAMETER. Never `SET x = '<literal>'`: that cannot be
--      -- parameterised and so invites string interpolation into SQL.
--   4. ALTER TABLE ... FORCE ROW LEVEL SECURITY (or keep app_rw a non-owner).
-- =====================================================================================
CREATE FUNCTION current_tenant() RETURNS text LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('audit_rail.tenant_id', true), '') $$;

DO $$ DECLARE r record; BEGIN
  FOR r IN
    SELECT c.relname FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
     WHERE n.nspname = 'public' AND c.relkind = 'r'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format($f$CREATE POLICY tenant_isolation ON %I
                      USING (tenant_id = current_tenant())
                      WITH CHECK (tenant_id = current_tenant())$f$, r.relname);
  END LOOP;
END $$;
