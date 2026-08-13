#!/usr/bin/env python3
"""Empty the blob vault — every uploaded file, for every tenant.

**Why this is not part of `init_db.py`, which is where you would expect it.**
`scripts/seed_e2e.py` subprocess-runs `init_db.py --force` with `DATABASE_URL` overridden
but **not** `VAULT_DIR`. If wiping the vault lived in that script, every `bash e2e.sh`
would silently delete the dev vault. Different lifecycle, different owner, different
command.

**Why the guards are heavier than for a database.** A database is a remote service named
by a URL you either can or cannot reach. A vault is a filesystem path named by a string
that can perfectly well be `$HOME` — the failure is silent, instant and total. So the
last guard does not check the path's NAME at all: it checks that the directory's
*contents* have the shape `api/storage.py:save` produces, `<tenant-uuid>/<uuid4hex><ext>`.
Pointed at `~/Documents` this aborts on the first child that is not a UUID. No amount of
path checking can do that.

Orphaning is safe in the other direction: every read path in the API looks the
`storage_key` up in the database first and already answers 410 (or degrades to "no logo")
when the file is gone — see `evidence.py`, `signing.py`, `org.py`, `documents.py`,
`branding.py`. So wiping the vault beside a blank database leaves nothing dangling.

Usage:
  .venv/bin/python scripts/reset_vault.py          # dry run — prints what it WOULD delete
  .venv/bin/python scripts/reset_vault.py --yes    # actually delete
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.core.config import settings  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
#: A server vault may legitimately live outside the checkout (`/srv/audit_rail/vault`),
#: so the inside-the-repo rule has to be escapable — but only on purpose.
ESCAPE_HATCH = "VAULT_RESET_ALLOW_OUTSIDE_REPO"


def die(msg: str) -> None:
    sys.exit(f"refusing to touch the vault — {msg}")


def resolve_vault() -> Path:
    raw = Path(settings.vault_dir)
    if raw.is_symlink():
        die(f"{raw} is a symlink; resolve it yourself and pass the real path via VAULT_DIR")
    if not raw.exists():
        sys.exit(f"vault directory does not exist: {raw}\nnothing to do.")
    path = raw.resolve()
    if not path.is_dir():
        die(f"{path} is not a directory")
    if path == Path(path.anchor) or path == Path.home() or path == REPO:
        die(f"{path} is a filesystem root, your home directory, or the repo itself")
    if len(path.parts) < 3:
        die(f"{path} is too close to the filesystem root to be a vault")
    if not path.is_relative_to(REPO) and os.environ.get(ESCAPE_HATCH) != "1":
        die(f"{path} is outside {REPO}.\n"
            f"  If that is genuinely the vault, re-run with {ESCAPE_HATCH}=1")
    return path


def survey(path: Path) -> tuple[list[Path], int, int]:
    """(tenant dirs, file count, total bytes) — and the shape check that makes this safe.

    `storage.save` writes `VAULT_DIR/<tenant_id>/<uuid4hex><ext>` and nothing else ever
    writes here, so any other shape means this is not the vault."""
    offenders: list[str] = []
    tenant_dirs: list[Path] = []
    files = 0
    total = 0
    for child in sorted(path.iterdir()):
        if not child.is_dir():
            offenders.append(f"{child.name} (not a directory)")
            continue
        try:
            uuid.UUID(child.name)
        except ValueError:
            offenders.append(f"{child.name} (not a tenant uuid)")
            continue
        tenant_dirs.append(child)
        for blob in child.rglob("*"):
            if blob.is_dir():
                offenders.append(f"{child.name}/{blob.name} (unexpected sub-directory)")
            else:
                files += 1
                total += blob.stat().st_size
    if offenders:
        die(f"{path} does not look like a vault. Unexpected entries:\n    "
            + "\n    ".join(offenders[:5])
            + (f"\n    …and {len(offenders) - 5} more" if len(offenders) > 5 else ""))
    return tenant_dirs, files, total


def main() -> None:
    ap = argparse.ArgumentParser(description="Empty the blob vault.")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete; without it this is a dry run")
    args = ap.parse_args()

    path = resolve_vault()
    tenant_dirs, files, total = survey(path)

    # Both coordinates printed together, because the whole point of running this is that
    # the vault and the database are being reset as a PAIR — and seeing one without the
    # other is how you wipe the files belonging to a database you meant to keep.
    print(f"  vault     {path}")
    print(f"  database  {settings.database_url.split('@')[-1]}")
    print(f"  contents  {len(tenant_dirs)} tenant dirs · {files} files · "
          f"{total / 1024 / 1024:.1f} MB")

    if not files and not tenant_dirs:
        print("\nAlready empty.")
        return
    if not args.yes:
        print("\nDry run. Re-run with --yes to delete all of the above.")
        return

    for child in tenant_dirs:
        shutil.rmtree(child)
    # The vault directory itself survives, with its mode and any mount intact — only its
    # children go. Recreating it would silently change ownership on a server.
    print(f"\nDeleted {files} files. {path} is now empty.")


if __name__ == "__main__":
    main()
