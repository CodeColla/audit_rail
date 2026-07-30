#!/usr/bin/env bash
# Run the Playwright browser suite.
#
#   ./e2e.sh                      # re-seed, then run everything
#   ./e2e.sh smoke                # only specs matching "smoke"
#   ./e2e.sh --no-seed 20-attest  # skip the re-seed (fast iteration on one spec)
#   ./e2e.sh --ui                 # Playwright's interactive mode
#   ./e2e.sh --headed --project=chromium
#
# WHY IT RE-SEEDS EVERY RUN: the suite mutates real state — documents get published,
# magic-link tokens get consumed, attestations get signed. Without a reset, a second
# run would find the token already used and the campaign with nobody left to invite.
# Re-seeding (~12s) buys a deterministic starting point; --no-seed skips it when you
# are iterating on a spec that does not care.
#
# Two machine-specific things this handles so you never have to:
#
#  1. NODE 22. The default `node` here is 18, and Playwright requires >= 20.
#     Node 22 is already installed via nvm, just not on PATH.
#  2. CHROMIUM LIBS. Chromium needs libnss3/libnspr4/libasound2, which aren't
#     installed system-wide and would need sudo. They were unpacked into
#     ~/.cache/audit-rail-pw-libs instead, and LD_LIBRARY_PATH points at them.
#     Nothing outside your home directory was modified. If that cache is ever
#     lost, rebuild it with:
#       mkdir -p ~/.cache/audit-rail-pw-libs/debs && cd $_ \
#         && apt-get download libnss3 libnspr4 libasound2t64 \
#         && for d in *.deb; do dpkg-deb -x "$d" ../root; done \
#         && ln -sf libasound.so.2.0.0 ../root/usr/lib/x86_64-linux-gnu/libasound.so.2
#
# The suite runs against its OWN stack (UI 3099 / API 5099 / audit_rail_e2e db),
# so your dev servers and dev data are untouched. Re-seed that database with:
#     .venv/bin/python scripts/seed_e2e.py
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_DIR="$HOME/.nvm/versions/node/v22.23.1/bin"
PW_LIBS="$HOME/.cache/audit-rail-pw-libs/root/usr/lib/x86_64-linux-gnu"

[ -d "$NODE_DIR" ] || { echo "Node 22 not found at $NODE_DIR — check 'ls ~/.nvm/versions/node'"; exit 1; }
export PATH="$NODE_DIR:$PATH"
[ -d "$PW_LIBS" ] && export LD_LIBRARY_PATH="$PW_LIBS:${LD_LIBRARY_PATH:-}"

SEED=1
ARGS=()
for a in "$@"; do
  if [ "$a" = "--no-seed" ]; then SEED=0; else ARGS+=("$a"); fi
done

if [ "$SEED" = "1" ]; then
  echo "→ re-seeding audit_rail_e2e for a deterministic run…"
  "$REPO/.venv/bin/python" "$REPO/scripts/seed_e2e.py" >/dev/null || {
    echo "seed failed — run it directly to see why: .venv/bin/python scripts/seed_e2e.py"; exit 1; }
fi

cd "$REPO/webui"
exec npx playwright test "${ARGS[@]}"
