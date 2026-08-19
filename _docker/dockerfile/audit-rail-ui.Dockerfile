# Audit Rail — UI image. Builds the SPA, then serves the static output with nginx.
#
# Build from the REPO ROOT — the COPY paths below are repo-relative, not webui-relative:
#     docker build -f _docker/dockerfile/audit-rail-ui.Dockerfile -t audit-rail-ui .
#
# There is no Node process at runtime. API_URL is read at CONTAINER START by entrypoint.sh and
# turned into nginx config, so the same image works against any backend: change the value in
# Portainer, restart, done. See _docker/ui/entrypoint.sh for why that is nginx and not
# JavaScript — it is not a style choice.

# Stage 1: Build
FROM node:20-slim AS build

WORKDIR /app

COPY webui/package.json ./
RUN npm install --force --silent

COPY webui/ ./
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:stable-alpine

COPY _docker/ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

COPY _docker/ui/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80
CMD ["/entrypoint.sh"]
