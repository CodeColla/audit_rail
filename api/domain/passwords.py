"""Password policy (P4-S1).

    • at least 8 characters, containing both letters and digits
    • expires after 30 days — the user must change it before continuing
    • the previous 3 passwords may not be reused

History lives in `user_password_history`, keyed by `level`: 0 = current, 1 = previous,
2 = the one before. A change shifts everyone down and drops what falls off the end, so a
user never has more than three rows. Expiry is measured from the level-0 row's `changed_at`.

Hashing stays argon2id via api.auth.hash_password — this module only owns the *policy*.
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import delete, insert, select, update

from api.core.auth import hash_password, verify_password
from api.core.database import t
from api.core.util import now_iso

MIN_LENGTH = 8
MAX_AGE_DAYS = 30
HISTORY_DEPTH = 3          # current + 2 previous == "cannot reuse the last 3"


def validate(password: str) -> None:
    """Raise ValueError with a human explanation if the password is unacceptable."""
    pw = password or ""
    if len(pw) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"[0-9]", pw):
        raise ValueError("password must contain both letters and numbers")


def assert_not_reused(conn, user_id: str, password: str) -> None:
    """Reject a password matching any of the retained hashes.

    Argon2 salts every hash, so this cannot be a lookup — each retained hash is verified
    against the candidate in turn. At most 3 comparisons.
    """
    h = t("user_password_history")
    for row in conn.execute(select(h.c.password_hash).where(h.c.user_id == user_id)).scalars():
        if verify_password(password, row):
            raise ValueError(
                f"that is one of your last {HISTORY_DEPTH} passwords — please choose a new one")


def set_password(conn, user_id: str, password: str) -> None:
    """Validate, reject reuse, then store — shifting the history down a level.

    Order matters: delete level 2 first so 1→2 and then 0→1 never collide with the
    UNIQUE (user_id, level) constraint mid-statement.
    """
    validate(password)
    assert_not_reused(conn, user_id, password)

    h = t("user_password_history")
    conn.execute(delete(h).where(h.c.user_id == user_id, h.c.level == 2))
    conn.execute(update(h).where(h.c.user_id == user_id, h.c.level == 1).values(level=2))
    conn.execute(update(h).where(h.c.user_id == user_id, h.c.level == 0).values(level=1))

    hashed, now = hash_password(password), now_iso()
    conn.execute(insert(h).values(user_id=user_id, password_hash=hashed,
                                  level=0, changed_at=now))
    conn.execute(update(t("users")).where(t("users").c.id == user_id)
                 .values(password_hash=hashed, status="active"))


def days_until_expiry(conn, user_id: str) -> int | None:
    """Days left before the password must be changed; None if we have no history row.

    A user with no level-0 row (seeded/legacy, or invited and never set) is NOT treated as
    expired — there is nothing to expire yet, and forcing a change on them would lock out
    the bootstrap admin.
    """
    h = t("user_password_history")
    changed = conn.execute(select(h.c.changed_at).where(
        h.c.user_id == user_id, h.c.level == 0)).scalar()
    if not changed:
        return None
    age = (dt.date.today() - dt.date.fromisoformat(changed[:10])).days
    return MAX_AGE_DAYS - age


def is_expired(conn, user_id: str) -> bool:
    left = days_until_expiry(conn, user_id)
    return left is not None and left < 0
