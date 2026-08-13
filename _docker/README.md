# `_docker/` — building and deploying Audit Rail

Everything needed to ship the product as two containers behind your own nginx, with your own
Postgres. **Nothing here touches `setup.sh` or `start.sh`** — those remain the local terminal
workflow and are unaffected.

```
_docker/
  dockerfile/  audit-rail-api.Dockerfile        python:3.12-slim + uvicorn
               audit-rail-ui.Dockerfile         node build -> nginx:alpine serving the SPA
  compose/     audit-rail-api.compose.yml       API service
               audit-rail-ui.compose.yml        UI service
               audit-rail-postgres.compose.yml  LOCAL DEV database only — not a deployment
  scripts/     service_ctl.sh                   build / up / down / logs / push, by numeric id
  ui/          nginx.conf                       the UI container's server block
               20-api-proxy.sh                  optional runtime /api forwarding, via API_URL
  env/         api.env.example                  copy to api.env and fill in
               ui.env.example                   copy to ui.env
  host.sql     GENERATED from db/schema.sql; apply once to your database
```

**One file per service** (issue #4, convention borrowed from the GINTI repo). The API and the
UI have separate compose files because they are routinely deployed to *different servers* — on
each one you bring up only what belongs there.

| ID | Component | Description | Port |
| -- | --------- | ----------- | ---- |
| 0  | all       | api + ui (never postgres) | — |
| 1  | api       | FastAPI backend | 5007 |
| 2  | ui        | SPA served by nginx | 8080 |
| 3  | postgres  | **Local dev database only** | 5434 |

```bash
./_docker/scripts/service_ctl.sh build 0     # build both images, tag derived from git
./_docker/scripts/service_ctl.sh up 1        # start the API
./_docker/scripts/service_ctl.sh logs 2      # follow the UI logs
REGISTRY=registry.example.com/you ./_docker/scripts/service_ctl.sh push 0
```

The tag is `<version>-<YYYYMMDD>`, where `<version>` is the git tag on HEAD if there is one and
the short sha otherwise. `-dirty` is appended when the working tree has uncommitted changes,
and `push` refuses such a build outright — an image built from unsaved work must never be
mistaken for a reproducible one.

## The shape this assumes

```
                    ┌─────────────────────────────────────┐
  browser ─────────►│  your nginx   ar.iam-kiam.com       │
                    │    /      ──► ui  container  :8080  │
                    │    /api   ──► api container  :5007  │
                    └─────────────────────────────────────┘
                                        │
                              your Postgres (external)
```

One origin as far as the browser is concerned, which is what keeps the whole product working
without a single frontend change — see "Why one origin matters" below.

## First deploy

**1. Database.** Create it yourself, then apply the schema once:

```bash
createdb -E UTF8 audit_rail          # must be UTF8; the schema refuses otherwise
psql -h <host> -U <user> -d audit_rail -f _docker/host.sql
```

81 tables, 49 triggers, 66 RLS policies, **zero rows**. That is complete, not partial: this
product has no global master data. The first signup seeds its own organisation — 3 roles with
234 permissions, ~94 vocabulary values, 16 domains, 95 controls and 3 frameworks — because only
4 of the 81 tables lack a `tenant_id`. A new organisation gets no assessment templates; those
are imported at **Audits → Import**.

**2. Build.**

```bash
./_docker/scripts/service_ctl.sh build 0                                   # both images
REGISTRY=registry.example.com/iesg ./_docker/scripts/service_ctl.sh push 0
```

Tag is `<version>-<YYYYMMDD>`, where version is the git tag on HEAD if there is one, else the
short commit sha, plus `-dirty` if the tree has uncommitted changes. `--push` refuses a dirty
build.

**3. Configure and run**, on each server:

```bash
cp _docker/env/api.env.example _docker/env/api.env    # fill in DATABASE_URL + JWT_SECRET
cp _docker/env/ui.env.example  _docker/env/ui.env
TAG=v1.0.0-20260812 ./_docker/scripts/service_ctl.sh up 1    # on the API server
TAG=v1.0.0-20260812 ./_docker/scripts/service_ctl.sh up 2    # on the UI server
```

**4. Your nginx.** The rules that matter, whatever syntax you express them in:

```nginx
client_max_body_size 30m;      # the default 1m breaks EVERY upload in the product

location / {
    proxy_pass http://127.0.0.1:8080;      # the ui container
}

location /api/ {
    proxy_pass http://127.0.0.1:5007;      # the api container — NO trailing slash
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;               # draft PDFs render inside the request
    proxy_buffering    off;                # stream 25 MB downloads
}
```

Four ways to get this wrong, all of which fail quietly:

- **A trailing slash on `proxy_pass`** for `/api/` strips the prefix and every route 404s. The
  FastAPI routers are mounted *at* `/api`; the prefix is real.
- **`client_max_body_size`** at the 1 MB default rejects evidence (25 MB), document images
  (5 MB) and logos (2 MB) with an nginx HTML error page the SPA shows as an opaque failure.
- **Caching `/api`.** Only 3 of 13 file routes send `Cache-Control`, and `/api/org/logo` has no
  tenant in its URL — the tenant comes from the JWT. A URL-keyed cache would serve one
  customer's logo to another. nginx does not cache unless you configure `proxy_cache`; do not.
- **Serving `/api` from the SPA fallback.** Keep it a separate `location`.

**5. Sign up** at `https://ar.iam-kiam.com/signup`.

## Why one origin matters

Three things in the product assume the browser sees the API on the same origin as the SPA, and
none of them can be configured away:

- the public signing page fetches `/api/sign/<token>` with a hardcoded root-relative path;
- its policy images are plain `<img src="/api/sign/…">` tags, which cannot carry a bearer token;
- stored document HTML contains literal `/api/documents/images/<uuid>` srcs that are folded
  into `content_sha256` and therefore cannot be rewritten after the fact.

Forwarding `/api` at the edge keeps all three true. Putting the API on its own subdomain would
break them, and CORS would not fix it.

If you would rather point your nginx at **one** upstream, set `API_URL` in `ui.env` and send the
whole domain to the UI container — its nginx then forwards `/api` onward. Same result, one less
rule in your config. `docker logs audit-rail-ui` prints which mode it started in.

## Things that will bite

**The vault volume is not optional.** Every uploaded file — evidence, policy documents, asset
photos, published PDFs, org logos — lives on disk under `VAULT_DIR`. Its default is inside the
source tree, which in a container is the image layer, so without the volume in
`compose/audit-rail-api.compose.yml` a redeploy destroys every file while leaving the `files`
rows behind: the
UI keeps listing evidence that 410s when you click it. Back this volume up.

**`CORS_ORIGINS` is parsed as JSON.** `CORS_ORIGINS=https://ar.iam-kiam.com` will not start the
app. It must be `["https://ar.iam-kiam.com"]`. (With one origin, CORS is never used — but the
value still has to parse.)

**`JWT_SECRET` must be set.** The default is a public string in the repo and the app only prints
a warning before starting. Anyone with it can mint a token for any user in any tenant.

**One scheduler.** `SCHEDULER_ENABLED` starts an in-process job runner with no shared lock that
*creates* task rows. Exactly one container may have it true, and keep `WEB_CONCURRENCY=1` while
it is — each uvicorn worker would start its own.

**HTTPS, not just eventually.** `navigator.clipboard` is undefined outside a secure context, so
on plain HTTP the "copy attestation link" and "copy auditor invite" buttons silently do nothing
while still flashing "Copied ✓". The bearer token is also a header on every request with no
HSTS behind it.

**`E2E_TEST_HOOKS` must stay unset.** At `1` it mounts test-only routes under `/api/e2e/*`.

## Upgrading

```bash
./_docker/scripts/service_ctl.sh build 0
./_docker/scripts/service_ctl.sh push 0
TAG=<new tag> ./_docker/scripts/service_ctl.sh up 0
```

Rebuild and re-apply `host.sql` only when the schema changes — and note there is no migration
framework: `host.sql` creates, it does not alter. Schema changes against a live database are
hand-written `ALTER`s (`api/main.py::_REQUIRED_COLUMNS` lists the ones the app checks for at
boot and will refuse to start without).

Roll back by starting the previous tag — the images are immutable and the tag names them.
