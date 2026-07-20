#!/bin/bash
# audit_rail — start the API (5007) and the web UI dev server (3002).
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -f .venv/bin/python ]; then
  echo "Virtual environment not found. Run: bash setup.sh"
  exit 1
fi

set -a; [ -f .env ] && source .env; set +a

cleanup() { kill 0 2>/dev/null; }
trap cleanup EXIT INT TERM

echo "[start] API  -> http://127.0.0.1:${API_PORT:-5007}"
.venv/bin/python -m uvicorn api.main:app \
  --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-5007}" --reload &

if [ -d webui/node_modules ]; then
  echo "[start] UI   -> http://127.0.0.1:3002  (login is prefilled, password 'audit_rail')"
  (cd webui && npm run dev) &
else
  echo "[start] web UI deps missing — run: cd webui && npm install"
fi

wait
