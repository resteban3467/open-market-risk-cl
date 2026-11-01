"""Structured logging and timing helpers for the risk workspace.

Enforces the ``observability-standards`` skill: every critical process emits
key-value traces (``valuation_date``, ``trade_count``, ``execution_time_ms``)
through structlog, never ``print()`` or plain ``logging``.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import structlog


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a structlog logger bound to ``name``."""
    return structlog.get_logger(name)


@contextmanager
def timed(
    logger: structlog.BoundLogger,
    event: str,
    **context: Any,
) -> Generator[None]:
    """Emit ``event`` with ``execution_time_ms`` and ``context`` on exit.

    The trace is emitted even when the wrapped block raises, so failed runs are
    observable too.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1_000.0
        logger.info(event, execution_time_ms=round(elapsed_ms, 3), **context)


__all__ = ["get_logger", "timed"]
