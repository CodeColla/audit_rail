# Changelog

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
(`_docker/env/*.env`), `host.sql` and the nginx assets did not move.

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
