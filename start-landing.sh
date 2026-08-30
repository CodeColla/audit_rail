#!/bin/bash
# audit_rail — start the landing page dev server (3003).
#
# Separate from start.sh on purpose: landingpage/ is a standalone site with no API dependency
# (see landingpage/src/lib/env.ts) — it doesn't need the API or the main UI running alongside it.
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -d landingpage/node_modules ]; then
  echo "Landing page deps missing. Run: cd landingpage && npm install"
  exit 1
fi

echo "[start-landing] Landing page -> http://127.0.0.1:3003"
(cd landingpage && npm run dev)
