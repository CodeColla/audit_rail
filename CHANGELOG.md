# Changelog

## Unreleased — bugfixes & enhancements across the portal ([#8](https://github.com/CodeColla/audit_rail/issues/8))

Seven independent fixes and additions found in day-to-day use, spanning the audit workspace,
the documents editor, and Admin · Masters.

### Action required if you deploy

Two schema changes, both hand-run (no Alembic) — run once against prod, in order, before
swapping the API/UI images:

```sql
BEGIN;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_classification_check;

CREATE TABLE IF NOT EXISTS response_documents (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    document_id text NOT NULL,
    PRIMARY KEY (response_id, document_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents (id, tenant_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS response_incidents (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    incident_id text NOT NULL,
    PRIMARY KEY (response_id, incident_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (incident_id, tenant_id) REFERENCES incidents (id, tenant_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS response_assets (
    tenant_id   text NOT NULL REFERENCES tenants(id),
    response_id text NOT NULL,
    asset_id    text NOT NULL,
    PRIMARY KEY (response_id, asset_id),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses (id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id)    REFERENCES assets    (id, tenant_id) ON DELETE CASCADE
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_response_documents_tenant') THEN
        CREATE TRIGGER trg_response_documents_tenant BEFORE INSERT ON response_documents
            FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_response_incidents_tenant') THEN
        CREATE TRIGGER trg_response_incidents_tenant BEFORE INSERT ON response_incidents
            FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_response_assets_tenant') THEN
        CREATE TRIGGER trg_response_assets_tenant BEFORE INSERT ON response_assets
            FOR EACH ROW EXECUTE FUNCTION inherit_tenant('responses', 'response_id');
    END IF;
END $$;

COMMIT;
```

Both are additive and metadata-only — dropping the CHECK doesn't touch existing rows, and the
three new tables have no relationship to existing data. Existing documents, their
classifications, and every evidence link already on an audit point are unaffected; verified
against the diff (0 lines removed anywhere in `api/routers/assessments.py`, and the only 2
lines removed in `db/schema.sql` are that one CHECK constraint).

**One-time backfill for existing orgs.** New signups get the `document_classification`
vocabulary automatically; orgs that signed up before this release need it backfilled once:

```sql
INSERT INTO lookup_values (tenant_id, kind, value, sort_order, is_active)
SELECT t.id, 'document_classification', v.value, v.ord, 1
FROM tenants t
CROSS JOIN (VALUES ('PUBLIC',0),('INTERNAL',1),('CONFIDENTIAL',2),('SECRET',3)) AS v(value, ord)
ON CONFLICT (tenant_id, kind, value) DO NOTHING;
```

No other schema or data changes. No new env vars, no new permissions.

### Fixed

- **The documents editor toolbar disappeared while scrolling.** Root cause was subtler than a
  z-index fight: the editor card's `overflow-hidden` (there to clip rounded corners) made it
  sticky positioning's *containing block* — but that card never scrolls itself, the page does,
  so "stick 57px from the top of a box that's always moving" was a no-op. Measured directly:
  the toolbar's offset tracked scroll position 1:1, never once clamping. Fixed by dropping
  `overflow-hidden` (corner-rounding moved to the two children that actually touch the card's
  edges) and offsetting the toolbar below the app header's own height.
- **Deleting an audit left its imported checklist behind.** `assessments.template_id` has no
  `ON DELETE` action by design (a checklist can outlive one year's audit) — but nothing in the
  product actually reuses a template for a second assessment today, so a deleted audit's
  checklist and crosswalk mappings were orphaned forever, still visible under Mappings and
  Controls → Bank crosswalk. Deleting an audit now also removes its template, but only when no
  other assessment still references it.

### Added

- **Table columns and rows can be added and deleted** in the documents editor, via a bubble
  menu that appears with the caret inside a table. The commands already shipped with
  `@tiptap/extension-table`; only the toolbar's fixed 3×3 insert was ever wired up.
- **Fullscreen view for Word-type documents**, matching the spreadsheet editor's own.
- **Document Classification is now an editable Masters vocabulary** instead of a closed,
  4-value database enum — Admin · Masters had no block for it at all. Seeded with the same 4
  values (`PUBLIC`/`INTERNAL`/`CONFIDENTIAL`/`SECRET`) so nothing already classified changes;
  an org can now add e.g. `RESTRICTED` without a migration. `data_items.classification` (the
  data inventory register) is a different column and is untouched.
- **An audit point can attach a Document, Incident, or Asset**, not just Evidence — three new
  join tables mirroring `response_evidence`'s own shape, and three new cards in the question
  drawer next to "Linked evidence."
- **An audit can be deleted.** The permission already existed (`audits.delete`); there was
  simply no route. Confirmation names the bank, question count, and warns that the checklist
  goes with it if unused elsewhere.
- **Checklist import now previews before writing anything.** A new `/templates/import/preview`
  step parses the file without touching the database; if every row already has a number
  (the common case), import proceeds straight through exactly as before. If any row is
  missing one — or no Number column was detected at all — an editable grid appears and commit
  is blocked until every row is fixed. Committing sends the (possibly hand-edited) rows
  verbatim; the file is never re-parsed a second time.
- **Audit question numbers are editable in place**, sort naturally, and no longer show a
  leading `#` (which read as an id rather than a sequence number). Stays free text — real bank
  checklists number points "3.a" or "1.2.3", not just integers.

### Verified

559 pytest · 189/189 Playwright, including the full pre-existing import-wizard golden path
unchanged · `tsc` clean throughout. Every change was also exercised in a real signed-up
browser session, not just asserted against.

## Unreleased — repository restructure ([#4](https://github.com/CodeColla/audit_rail/issues/4))

Housekeeping only. **No API change, no schema change, no dependency change, no behaviour
change.** Every moved file kept its contents; only paths and imports moved.

### Action required if you deploy

| Was | Now |
| --- | --- |
| `./_docker/build.sh` | `./_docker/scripts/service_ctl.sh build 0` |
| `./_docker/build.sh --push` | `./_docker/scripts/service_ctl.sh push 0` |
| `cd _docker && TAG=… docker compose up -d` | `TAG=… ./_docker/scripts/service_ctl.sh up 0` |
| `docker-compose.yml` (repo root) | `_docker/compose/audit-rail-postgres.compose.yml` |
| `_docker/docker-compose.yml` | split into `audit-rail-api.compose.yml` + `audit-rail-ui.compose.yml` |
| `_docker/Dockerfile.api` / `.ui` | `_docker/dockerfile/audit-rail-{api,ui}.Dockerfile` |

Component IDs for the control script: **0** all · **1** api · **2** ui · **3** postgres
(local dev only — `0` never includes it). `./_docker/scripts/service_ctl.sh` with no arguments
prints the table.

`setup.sh` was updated in the same commit and needs nothing from you. Env files
(`_docker/env/*.env`) and `host.sql` did not move.

**Three further deployment changes**, made after the restructure to bring the compose files
onto GINTI's shape:

1. **No more `TAG=` on `up`.** The compose files now name the image bare (`audit-rail-api`), so
   `build` tags `:latest` alongside the versioned tag. `push` still ships the versioned one.
2. **The `audit-rail` network is external.** `service_ctl.sh up` creates it; by hand it is
   `docker network create audit-rail`. This is what lets `API_URL=http://audit-rail-api:5007`
   resolve from the UI container.
3. **You must supply vault storage yourself.** The API compose file no longer declares a
   volume, so `/data/vault` lands in an *anonymous* one and the next container recreate starts
   empty while the `files` rows survive — the UI then lists evidence that 410s. Uncomment a
   named volume or a host path in `audit-rail-api.compose.yml` before real data goes in.

`_docker/ui/20-api-proxy.sh` became `_docker/ui/entrypoint.sh` (same job, GINTI's shape: write
config, then `exec nginx`). `API_URL` behaves exactly as before — a runtime nginx upstream, read
at container start, so changing it in Portainer and restarting is enough. It is deliberately
**not** a base URL handed to the frontend: `document_versions.content_sha256` is
`GENERATED ALWAYS` over stored HTML that contains literal `/api/documents/images/<uuid>` srcs
and is frozen on publish, so a second origin could never be migrated away from.

### Changed

- **`_docker/` follows one file per service**: `dockerfile/`, `compose/`, `scripts/`. The two
  `docker-compose.yml` files that could be mistaken for one another are gone — the local dev
  database and the deployed stack are now named unambiguously.
- **`service_ctl.sh`** replaces `build.sh`, adding `build | up | down | logs | push` by numeric
  ID. It keeps `build.sh`'s version derivation: the tag is `<git tag on HEAD, else short
  sha>-<YYYYMMDD>`, with `-dirty` appended for an uncommitted tree — and `push` refuses a dirty
  build outright.
- **`api/` is three packages** beside `routers/`: `core/` (config, database, auth, permissions,
  storage, util, activity) · `domain/` (control_library, domains, frameworks, vocabularies,
  importer, scoring, tasks_engine, mapping, passwords, gstin) · `rendering/` (render,
  docx_export, xlsx_io, html_sanitize, imagefile, branding). Was 26 flat modules.
- **`webui/src/pages/` is grouped by feature**: `documents · controls · registers · audits ·
  people · admin · auth`. Was 29 flat files. Shared `components/` and `lib/` are unchanged.
- `README.md` and `_docker/README.md` updated to match.

### Verified

545 pytest · 188/189 Playwright (the one failure passes in isolation — see the PR notes) ·
`tsc` and `npm run build` clean · all three compose files pass `docker compose config`.
**`docker build` itself was not run** — no daemon access in the environment this was done in.

### Removed

- `webui/src/pages/Policies.tsx.retired` — nothing imported it, and that is what version
  control is for.
