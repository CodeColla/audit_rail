#!/bin/bash
# audit_rail — first-time setup. Run once: bash setup.sh
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY=python3
if ! $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "ERROR: python3 >= 3.10 required (found: $($PY --version 2>&1))"
  exit 1
fi

echo "[1] Creating .venv and installing API dependencies..."
$PY -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r api/requirements.txt -q

echo "[2] Starting PostgreSQL (docker compose — host port 5434)..."
# issue #4: the compose files moved into _docker/compose/, one per service, so this names the
# file explicitly instead of relying on a docker-compose.yml at the repo root.
PG_COMPOSE="_docker/compose/audit-rail-postgres.compose.yml"
if docker compose -f "$PG_COMPOSE" up -d 2>/dev/null \
   || docker-compose -f "$PG_COMPOSE" up -d 2>/dev/null; then
  # wait for the healthcheck rather than racing it
  for i in $(seq 1 30); do
    if docker compose -f "$PG_COMPOSE" exec -T postgres pg_isready -U audit -d audit_rail >/dev/null 2>&1; then
      echo "    postgres ready on 127.0.0.1:5434"
      break
    fi
    sleep 1
  done
else
  echo "    !! could not run docker compose — start Postgres yourself, then re-run this script."
  echo "       Expected: postgres on 127.0.0.1:5434, db=audit_rail, user=audit, pass=audit"
  exit 1
fi

echo "[3] Initialising schema + seed data..."
.venv/bin/python scripts/init_db.py --force
.venv/bin/python scripts/build_control_library.py   # curated controls + crosswalk
.venv/bin/python scripts/set_dev_password.py        # dev login (password: audit_rail)
.venv/bin/python scripts/seed_demo.py               # demo tasks/evidence/policies

echo "[4] Web UI dependencies..."
if command -v npm >/dev/null 2>&1; then
  (cd webui && npm install --silent)
else
  echo "    npm not found — skip; install Node 18+ then: cd webui && npm install"
fi

echo ""
echo "Setup complete. Start everything with: bash start.sh"
echo "  API  http://127.0.0.1:5007      UI  http://127.0.0.1:3002"
echo "  login: sumit.t@iesglabs.com — set a password first:"
echo "         .venv/bin/python scripts/set_dev_password.py"
echo ""
echo "  For a from-scratch walkthrough instead (no seeded tenant at all):"
echo "         .venv/bin/python scripts/init_db.py --force --blank"
echo "         .venv/bin/python scripts/reset_vault.py --yes"
echo "         then sign up at http://127.0.0.1:3002/signup"
