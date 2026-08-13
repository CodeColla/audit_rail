#!/usr/bin/env bash
# Audit Rail — unified build/deploy control.  (issue #4; convention borrowed from GINTI)
#
#   ./_docker/scripts/service_ctl.sh <command> <id>
#
#   Commands   build   Build the image(s). Tag is derived from git — no argument needed.
#              up      Start the service(s) with docker compose
#              down    Stop them
#              logs    Follow the logs
#              push    Push built images (needs REGISTRY, refuses a dirty tree)
#
#   IDs        0  all          1  api          2  ui          3  postgres (local dev only)
#
#   REGISTRY=registry.example.com/you ./_docker/scripts/service_ctl.sh push 0
#
# Replaces _docker/build.sh. The two things worth keeping from it are kept verbatim: the
# version derivation below, and the host.sql regeneration that stops the shipped schema
# drifting from db/schema.sql.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_DIR="${REPO}/_docker/compose"
cd "$REPO"

REGISTRY="${REGISTRY:-}"                 # e.g. registry.example.com/iesg  (no trailing slash)
API_IMAGE="${API_IMAGE:-audit-rail-api}"
UI_IMAGE="${UI_IMAGE:-audit-rail-ui}"

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'

usage() {
  cat <<EOF
${BOLD}Audit Rail — service control${NC}

  $0 <build|up|down|logs|push> <id>

  ${BOLD}ID   Component   Description                        Port${NC}
  0    all         Every component                     —
  1    api         FastAPI backend                     5007
  2    ui          SPA served by nginx                 8080
  3    postgres    ${DIM}Local dev database only${NC}             5434

  The image tag is derived from git automatically — see below.
EOF
}

[ $# -lt 1 ] && { usage; exit 2; }
CMD="$1"; TARGET="${2:-}"
case "$CMD" in -h|--help|help) usage; exit 0 ;; esac
[ -z "$TARGET" ] && { echo "${RED}missing id${NC}"; usage; exit 2; }

# id -> service name
case "$TARGET" in
  0) SERVICES=(api ui) ;;                # postgres is dev-only; `all` never touches it
  1) SERVICES=(api) ;;
  2) SERVICES=(ui) ;;
  3) SERVICES=(postgres) ;;
  *) echo "${RED}unknown id: $TARGET${NC}"; usage; exit 2 ;;
esac

compose_file() { echo "${COMPOSE_DIR}/audit-rail-$1.compose.yml"; }

# ── the version ────────────────────────────────────────────────────────────────────────
# A release is a tag ON THIS COMMIT. `--exact-match` is deliberate: `git describe` without it
# reports the most recent tag anywhere in history, so a build three commits after v1.2.0 would
# claim to be v1.2.0.
derive_tag() {
  git rev-parse --git-dir >/dev/null 2>&1 || { echo "${RED}not a git repository${NC}" >&2; exit 1; }
  if VERSION=$(git describe --tags --exact-match HEAD 2>/dev/null); then
    KIND="release tag"
  else
    VERSION=$(git rev-parse --short HEAD); KIND="commit (no tag on HEAD)"
  fi
  # `-dirty` so an image built from uncommitted work can never be mistaken for a
  # reproducible one — and `push` refuses it outright.
  if [ -n "$(git status --porcelain)" ]; then VERSION="${VERSION}-dirty"; DIRTY=1; else DIRTY=0; fi
  TAG="${VERSION}-$(date +%Y%m%d)"
  PREFIX=""; [ -n "$REGISTRY" ] && PREFIX="${REGISTRY%/}/"
  echo "  version   ${VERSION}   (${KIND})"
  echo "  tag       ${TAG}"
  [ "$DIRTY" = 1 ] && echo "  ${YELLOW}WARNING   working tree is dirty — this image is not reproducible${NC}"
  echo
}

# ── host.sql, regenerated so it can never drift from db/schema.sql ─────────────────────
generate_host_sql() {
  local out="$REPO/_docker/host.sql"
  {
    echo "-- ====================================================================="
    echo "-- Audit Rail — database schema for a hosted deployment."
    echo "--"
    echo "-- GENERATED FROM db/schema.sql BY _docker/scripts/service_ctl.sh — do not edit."
    echo "--   source commit : $(git rev-parse HEAD)"
    echo "--   version       : ${VERSION}"
    echo "--   generated     : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "--"
    echo "-- Run this INSIDE an already-created database, as its owner:"
    echo "--     psql -h <host> -U <user> -d <database> -f host.sql"
    echo "--"
    echo "-- It creates every table, trigger, index and RLS policy and NOTHING else — no"
    echo "-- CREATE DATABASE, no roles, no extensions (none are needed), and no seed rows."
    echo "-- That is correct rather than incomplete: this product has no global master data."
    echo "-- Signing up an organisation seeds its OWN roles, vocabularies, 16 domains, 95"
    echo "-- controls and 3 frameworks (api/routers/auth.py::_create_org), and only 4 of the"
    echo "-- tables below lack a tenant_id. So the first signup builds its own world."
    echo "--"
    echo "-- The database MUST be UTF8 — the schema refuses to install otherwise, because"
    echo "-- content hashes that back electronic signatures assume it."
    echo "-- ====================================================================="
    echo
    cat "$REPO/db/schema.sql"
  } > "$out"
  echo "  host.sql  $(wc -l < "$out") lines"
}

image_for() { case "$1" in api) echo "$API_IMAGE" ;; ui) echo "$UI_IMAGE" ;; esac; }

build_one() {
  local svc="$1" image; image="$(image_for "$svc")"
  echo "${CYAN}── building ${svc} ────────────────────────────────────────${NC}"
  # Context is the REPO ROOT for both images: the API needs api/db/scripts, and the UI's build
  # stage needs webui/. .dockerignore is what keeps that context small.
  docker build \
    -f "_docker/dockerfile/audit-rail-${svc}.Dockerfile" \
    -t "${PREFIX}${image}:${TAG}" \
    --label "org.opencontainers.image.version=${VERSION}" \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "org.opencontainers.image.title=Audit Rail ${svc}" \
    "$REPO"
  echo
}

usage
case "$CMD" in
  build)
    derive_tag; generate_host_sql; echo
    for s in "${SERVICES[@]}"; do
      [ "$s" = postgres ] && { echo "${DIM}postgres uses a stock image — nothing to build${NC}"; continue; }
      build_one "$s"
    done
    echo "${GREEN}Done.${NC}  TAG=${TAG}"
    echo "  Deploy:  TAG=${TAG} $0 up ${TARGET}"
    ;;
  push)
    derive_tag
    [ -z "$REGISTRY" ] && { echo "${RED}push needs REGISTRY set${NC}" >&2; exit 2; }
    [ "$DIRTY" = 1 ] && { echo "${RED}refusing to push a dirty build — commit first${NC}" >&2; exit 2; }
    for s in "${SERVICES[@]}"; do
      [ "$s" = postgres ] && continue
      docker push "${PREFIX}$(image_for "$s"):${TAG}"
    done
    ;;
  up|down|logs)
    for s in "${SERVICES[@]}"; do
      f="$(compose_file "$s")"
      [ -f "$f" ] || { echo "${RED}no compose file: $f${NC}" >&2; exit 1; }
      case "$CMD" in
        up)   docker compose -f "$f" up -d ;;
        down) docker compose -f "$f" down ;;
        logs) docker compose -f "$f" logs -f ;;
      esac
    done
    ;;
  *) echo "${RED}unknown command: $CMD${NC}"; usage; exit 2 ;;
esac
