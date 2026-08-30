# Audit Rail — landing page image. Builds the static marketing site, then serves it with nginx.
#
# Build from the REPO ROOT — the COPY paths below are repo-relative, not landingpage-relative:
#     docker build -f _docker/dockerfile/audit-rail-landing.Dockerfile -t audit-rail-landing .
#
# Simpler than the UI image: no entrypoint.sh, no runtime config. This page has no backend
# dependency — VITE_SIGNUP_URL is baked into the bundle at BUILD time (see
# landingpage/src/lib/env.ts) because it's an absolute, cross-origin URL, not an nginx upstream
# the way the UI's API_URL is. Changing it means rebuilding this image.

# Stage 1: Build
FROM node:20-slim AS build

WORKDIR /app

COPY landingpage/package.json ./
RUN npm install --force --silent

COPY landingpage/ ./
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:stable-alpine

COPY _docker/landing/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
