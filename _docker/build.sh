#!/usr/bin/env bash
# Build the Audit Rail images, and regenerate _docker/host.sql from db/schema.sql.
#
#   ./_docker/build.sh                  # build both, tag <version>-<date>
#   ./_docker/build.sh api              # just the API
#   ./_docker/build.sh ui               # just the UI
#   ./_docker/build.sh --push           # build both and push
#   REGISTRY=registry.example.com/iesg ./_docker/build.sh --push
#
# The tag is  <version>-<YYYYMMDD>  where <version> is the git tag on HEAD if there is one
# (a release), otherwise the short commit sha. `-dirty` is appended when the working tree has
# uncommitted changes, so an image built from unsaved work can never be mistaken for a
# reproducible one.
#
#   v1.2.0-20260812          built from the release tag v1.2.0
#   9a06478-20260812         built from a commit with no tag
#   9a06478-dirty-20260812   built with uncommitted changes  <- do not ship this
#
# This script does NOT touch setup.sh or start.sh — those stay the local terminal workflow.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

REGISTRY="${REGISTRY:-}"                 # e.g. registry.example.com/iesg  (no trailing slash)
API_IMAGE="${API_IMAGE:-audit-rail-api}"
UI_IMAGE="${UI_IMAGE:-audit-rail-ui}"

PUSH=0
TARGETS=()
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    api|ui) TARGETS+=("$arg") ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (expected: api, ui, --push)" >&2; exit 2 ;;
  esac
done
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=(api ui)

# ── work out the tag ───────────────────────────────────────────────────────────────────
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "not a git repository — cannot derive a version" >&2; exit 1
fi

# A release is a tag ON THIS COMMIT. `--exact-match` is deliberate: `git describe` without it
# would report the most recent tag anywhere in history, so a build three commits after v1.2.0
# would claim to be v1.2.0.
if VERSION=$(git describe --tags --exact-match HEAD 2>/dev/null); then
  KIND="release tag"
else
  VERSION=$(git rev-parse --short HEAD)
  KIND="commit (no tag on HEAD)"
fi
if [ -n "$(git status --porcelain)" ]; then
  VERSION="${VERSION}-dirty"
  DIRTY=1
else
  DIRTY=0
fi

DATE="$(date +%Y%m%d)"
TAG="${VERSION}-${DATE}"
PREFIX=""
[ -n "$REGISTRY" ] && PREFIX="${REGISTRY%/}/"

echo "  version   $VERSION   ($KIND)"
echo "  tag       $TAG"
echo "  images    ${PREFIX}${API_IMAGE}:${TAG}"
echo "            ${PREFIX}${UI_IMAGE}:${TAG}"
[ "$DIRTY" = 1 ] && echo "  WARNING   working tree is dirty — this image is not reproducible"
echo

# ── host.sql, regenerated so it can never drift from the schema ────────────────────────
generate_host_sql() {
  local out="$REPO/_docker/host.sql"
  {
    echo "-- ====================================================================="
    echo "-- Audit Rail — database schema for a hosted deployment."
    echo "--"
    echo "-- GENERATED FROM db/schema.sql BY _docker/build.sh — do not edit by hand."
    echo "--   source commit : $(git rev-parse HEAD)"
    echo "--   version       : $VERSION"
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
  echo "  host.sql  $(wc -l < "$out") lines  ->  _docker/host.sql"
}
generate_host_sql
echo

# ── build ──────────────────────────────────────────────────────────────────────────────
build_one() {
  local name="$1" dockerfile="$2" image="$3"
  echo "── building $name ────────────────────────────────────────────"
  # Context is the REPO ROOT for both images: the API needs api/db/scripts, and the UI's build
  # stage needs webui/. .dockerignore is what keeps that context small.
  docker build \
    -f "$dockerfile" \
    -t "${PREFIX}${image}:${TAG}" \
    --label "org.opencontainers.image.version=$VERSION" \
    --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "org.opencontainers.image.title=Audit Rail ${name}" \
    "$REPO"
  echo
}

for target in "${TARGETS[@]}"; do
  case "$target" in
    api) build_one "API" "_docker/Dockerfile.api" "$API_IMAGE" ;;
    ui)  build_one "UI"  "_docker/Dockerfile.ui"  "$UI_IMAGE" ;;
  esac
done

if [ "$PUSH" = 1 ]; then
  [ -z "$REGISTRY" ] && { echo "--push needs REGISTRY set" >&2; exit 2; }
  [ "$DIRTY" = 1 ] && { echo "refusing to push a dirty build — commit first" >&2; exit 2; }
  for target in "${TARGETS[@]}"; do
    case "$target" in
      api) docker push "${PREFIX}${API_IMAGE}:${TAG}" ;;
      ui)  docker push "${PREFIX}${UI_IMAGE}:${TAG}" ;;
    esac
  done
fi

cat <<EOF
Done.

  TAG=$TAG

Deploy with:
  cd _docker && TAG=$TAG docker compose up -d          # both services
  cd _docker && TAG=$TAG docker compose up -d api      # API server only
  cd _docker && TAG=$TAG docker compose up -d ui       # UI server only

First time on a server, copy the env templates and fill them in:
  cp _docker/env/api.env.example  _docker/env/api.env
  cp _docker/env/ui.env.example   _docker/env/ui.env
EOF
