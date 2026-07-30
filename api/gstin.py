"""GSTIN validation — the anti-duplicate key for an organisation (P4-S1).

A GSTIN is 15 characters:

    27  AAPFU0939F  1  Z  V
    ^   ^           ^  ^  ^
    |   |           |  |  check digit
    |   |           |  'Z' by default
    |   |           entity number for that PAN in the state (1-9, A-Z)
    |   the holder's 10-character PAN
    state code, 01-38

We validate the shape AND the check digit. Uniqueness alone is not enough protection: a
mistyped-but-well-formed number would create a second organisation for the same company,
which is exactly what the GST field exists to prevent.
"""

from __future__ import annotations

import re

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# state code | PAN (5 alpha, 4 digit, 1 alpha) | entity | Z | checksum
_SHAPE = re.compile(r"^[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def checksum(first14: str) -> str:
    """The 15th character, per the GSTN algorithm.

    Each of the first 14 characters is weighted alternately 1, 2, 1, 2…; every product is
    folded (quotient + remainder over 36) before summing, and the check digit is whatever
    brings the total up to a multiple of 36.
    """
    total = 0
    for i, ch in enumerate(first14):
        product = _ALPHABET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def normalise(value: str) -> str:
    """Upper-case and strip spaces/hyphens people paste in from invoices."""
    return re.sub(r"[\s-]", "", (value or "")).upper()


def validate(value: str) -> str:
    """Return the normalised GSTIN, or raise ValueError explaining what is wrong."""
    gstin = normalise(value)
    if not gstin:
        raise ValueError("GST number is required")
    if len(gstin) != 15:
        raise ValueError(f"a GST number is 15 characters; this one has {len(gstin)}")
    if not _SHAPE.match(gstin):
        raise ValueError("that does not look like a GST number (expected e.g. 27AAPFU0939F1ZV)")
    state = int(gstin[:2])
    if not 1 <= state <= 38:
        raise ValueError(f"{gstin[:2]} is not a valid GST state code")
    if gstin[14] != checksum(gstin[:14]):
        raise ValueError("that GST number's check digit is wrong — please re-check it")
    return gstin
