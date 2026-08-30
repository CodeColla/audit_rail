# `_docker/` — building and deploying Audit Rail

Everything needed to ship the product as two containers behind your own nginx, with your own
Postgres. **Nothing here touches `setup.sh` or `start.sh`** — those remain the local terminal
workflow and are unaffected.

```
_docker/
  dockerfile/  audit-rail-api.Dockerfile        python:3.12-slim + uvicorn
               audit-rail-ui.Dockerfile         node build -> nginx:alpine serving the SPA
               audit-rail-landing.Dockerfile    node build -> nginx:alpine serving the landing site
  compose/     audit-rail-api.compose.yml       API service
               audit-rail-ui.compose.yml        UI service
               audit-rail-landing.compose.yml   Landing page service
               audit-rail-postgres.compose.yml  LOCAL DEV database only — not a deployment
  scripts/     service_ctl.sh                   build / up / down / logs / push, by numeric id
  ui/          nginx.conf                       the UI container's server block
               entrypoint.sh                    writes the /api forwarding block from API_URL,
                                                then execs nginx
  landing/     nginx.conf                       the landing container's server block — no
                                                entrypoint.sh, no runtime config
  env/         api.env.example                  copy to api.env and fill in
               ui.env.example                   copy to ui.env
  host.sql     GENERATED from db/schema.sql; apply once to your database
```

**One file per service** (issue #4, convention borrowed from the GINTI repo). The API and the
UI have separate compose files because they are routinely deployed to *different servers* — on
each one you bring up only what belongs there.

| ID | Component | Description | Port |
| -- | --------- | ----------- | ---- |
| 0  | all       | api + ui + landing (never postgres) | — |
| 1  | api       | FastAPI backend | 5007 |
| 2  | ui        | SPA served by nginx | 8080 |
| 3  | postgres  | **Local dev database only** | 5434 |
| 4  | landing   | Static marketing site served by nginx | 8081 |

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
cp _docker/env/ui.env.example  _docker/env/ui.env    # API_URL: see the two modes in the file
./_docker/scripts/service_ctl.sh up 1                # on the API server
./_docker/scripts/service_ctl.sh up 2                # on the UI server
```

`up` resolves the image bare (`audit-rail-api`, i.e. `:latest`), which `build` tags alongside
the versioned one — so there is no `TAG=` to remember. It also creates the shared `audit-rail`
network on first run; the compose files declare it `external` so that the UI container can
reach the API by name when both sit on one host.

**The default deploy model is build-on-host**, which is what a bare `image:` plus a `build:`
section means: `up` builds when the image is absent. `push` still works and now ships both
tags, but the compose files never reference `${REGISTRY}` — to deploy from a registry, pull and
retag on the host first (`docker pull <registry>/audit-rail-api:latest && docker tag … audit-rail-api:latest`).

**Before the second deploy, add vault storage.** The compose files ship without a volume, so
`/data/vault` lands in an *anonymous* one and the next container recreate starts empty — see
"The vault" below. Uncomment one of the two lines in `audit-rail-api.compose.yml`.

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

`API_URL` is read at container start by `_docker/ui/entrypoint.sh`, which writes the `/api`
block and then execs nginx — so it is a **runtime** setting. Change it in Portainer on a
running container, restart, and it takes effect; nothing is baked into the bundle.

It is worth saying plainly what `API_URL` is *not*, because the obvious-looking alternative is
what the GINTI webui does and it cannot work here: it is an **nginx upstream**, never a base
URL handed to the frontend. Writing `window.__env__` and letting axios hold an absolute origin
would break the first two items above and permanently corrupt the third — `content_sha256` is
`GENERATED ALWAYS` over the stored HTML and frozen on publish, so those image URLs can never
be migrated.

## Things that will bite

**The vault volume is not optional, and the compose file ships without one.** Every uploaded
file — evidence, policy documents, asset photos, published PDFs, org logos — lives on disk
under `VAULT_DIR` (`/data/vault` in the image). The API Dockerfile declares
`VOLUME ["/data/vault"]`, so with nothing named in `compose/audit-rail-api.compose.yml` Docker
allocates an **anonymous** volume: the next container recreate gets a fresh one, the old is
orphaned rather than deleted, and the UI keeps listing evidence that 410s when you click it
because the `files` rows survive. Uncomment a named volume or a host path before real data
goes in, and back it up.

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

## The landing page

`landingpage/` is a separate, standalone site — the public marketing page, not the product.
It builds and serves the same way as the UI (`node build -> nginx:alpine`), but simpler:

- **No runtime config, no `entrypoint.sh`.** The one external reference the page makes — the
  sign-up CTA's URL — is an absolute, cross-origin link (`VITE_SIGNUP_URL`), baked into the
  bundle at **build** time via `landingpage/.env.example`. Changing it means rebuilding the
  image with `service_ctl.sh build 4`, not restarting the container.
- **No shared network.** Unlike the UI, this page has no backend to reach by container name,
  so `audit-rail-landing.compose.yml` never joins the `audit-rail` network.
- **Own port**, 8081, so it can run alongside the UI's 8080 on the same host if you want one
  server fronting both.

```bash
./_docker/scripts/service_ctl.sh build 4
./_docker/scripts/service_ctl.sh up 4
./_docker/scripts/service_ctl.sh logs 4
```

Point your own nginx (or whatever serves your public domain) at `127.0.0.1:8081` the same way
the UI section above describes — this container needs nothing else in front of it.

## Upgrading

```bash
./_docker/scripts/service_ctl.sh build 0
./_docker/scripts/service_ctl.sh push 0
./_docker/scripts/service_ctl.sh up 0
```

Rebuild and re-apply `host.sql` only when the schema changes — and note there is no migration
framework: `host.sql` creates, it does not alter. Schema changes against a live database are
hand-written `ALTER`s (`api/main.py::_REQUIRED_COLUMNS` lists the ones the app checks for at
boot and will refuse to start without).

Roll back by starting the previous tag — the images are immutable and the tag names them.
