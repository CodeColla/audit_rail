# Audit Rail — UI image. Builds the SPA, then serves the static output with nginx.
#
# Build from the REPO ROOT:
#     docker build -f _docker/dockerfile/audit-rail-ui.Dockerfile -t audit-rail-ui:TAG .
#
# The SPA is a static bundle — there is no Node process at runtime. The `API_URL` env var is
# read at CONTAINER START by an entrypoint script, not baked in at build time, so the same
# image works against any backend: stop it in Portainer, change the value, start it again.
# See _docker/ui/20-api-proxy.sh for how, and why that is nginx config rather than JavaScript.

# ── build ──────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /src

# `npm ci` needs both files and installs devDependencies too — `npm run build` is
# `tsc --noEmit && vite build`, and the typecheck needs them.
COPY webui/package.json webui/package-lock.json ./
RUN npm ci

COPY webui/ ./
RUN npm run build

# ── serve ──────────────────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Replaces the stock server block. See the file for the history fallback, which is what makes
# /sign/<token> and every deep link survive a cold page load.
COPY _docker/ui/nginx.conf /etc/nginx/conf.d/default.conf

# The official image executes everything in /docker-entrypoint.d/ before starting nginx.
COPY --chmod=755 _docker/ui/20-api-proxy.sh /docker-entrypoint.d/20-api-proxy.sh

# `nginx.conf` does `include /etc/nginx/api-proxy/*.conf;` inside its server block. The glob
# matching nothing is not an error, so an empty directory means "no proxy" — which is the
# right default when the reverse proxy in front routes /api itself.
RUN mkdir -p /etc/nginx/api-proxy

COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1
