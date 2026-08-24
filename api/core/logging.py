"""Structured logging (P7 / issue #5).

`loguru`, not stdlib `logging` — the deliberate choice, matching the reference repo's own
convention (GINTI). One shared `configure_logging()`, called once from `api/main.py`'s
`lifespan()`; call sites elsewhere just `from loguru import logger` — it's a global singleton,
not `getLogger(__name__)`.

This is a NEW convention as of P7, not a codification of prior practice: audit_rail had zero
logging infrastructure before this — just two `print()` calls in `main.py`. It applies going
forward (new code, and existing code when it's next touched for other reasons), not as a
retrofit sweep across every router.
"""

from __future__ import annotations

import sys

from loguru import logger


def configure_logging(level: str = "INFO") -> None:
    """Point loguru at stderr with a consistent format. Safe to call more than once —
    `logger.remove()` clears any prior handler first, so a second call (e.g. in a test
    fixture that re-imports the app) never doubles output."""
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    )
    logger.remove()
    logger.add(sys.stderr, level=level, format=fmt, colorize=True)


__all__ = ["configure_logging", "logger"]
