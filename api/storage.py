"""Local file vault (decision D4).

Files are stored on disk under VAULT_DIR/<tenant_id>/<uuid><ext> and referenced
by a `storage_key` (the relative path). Swapping to S3 later means reimplementing
this module against the same interface — nothing else changes.

Downloads MUST go through the API (access-controlled), never a static mount.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from api.config import settings

VAULT = Path(settings.vault_dir)


def save(tenant_id: str, data: bytes, original_name: str) -> tuple[str, str, int]:
    """Write bytes to the vault. Returns (storage_key, sha256_hex, size_bytes)."""
    ext = Path(original_name).suffix[:12]
    key = f"{tenant_id}/{uuid.uuid4().hex}{ext}"
    dest = VAULT / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return key, hashlib.sha256(data).hexdigest(), len(data)


def path_for(storage_key: str) -> Path:
    p = (VAULT / storage_key).resolve()
    # containment guard: never resolve outside the vault
    if not str(p).startswith(str(VAULT.resolve())):
        raise ValueError("invalid storage key")
    return p


def delete(storage_key: str) -> None:
    try:
        path_for(storage_key).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
