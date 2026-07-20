#!/usr/bin/env python3
"""Set a known dev password on the seeded users so login works locally.

Not for production — real users get an invite flow later.
Usage: .venv/bin/python scripts/set_dev_password.py [password]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text  # noqa: E402

from _db import get_engine  # noqa: E402
from api.auth import hash_password  # noqa: E402

PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "audit_rail"


def main() -> None:
    engine = get_engine()
    pw = hash_password(PASSWORD)
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE users SET password_hash = :p, status = 'active' "
            "WHERE auth_provider = 'local'"), {"p": pw})
        rows = conn.execute(text(
            "SELECT email FROM users WHERE status='active' ORDER BY email")).scalars().all()
    print(f"Set password on {res.rowcount} user(s): password = {PASSWORD!r}")
    for email in rows:
        print(f"    {email}")


if __name__ == "__main__":
    main()
