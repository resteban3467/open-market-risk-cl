"""Ports for the trade/leg contract repository."""

from __future__ import annotations

from datetime import date
from typing import Protocol

import polars as pl


class TradeRepositoryPort(Protocol):
    """Read-only access to the ``dim_trades`` / ``dim_legs`` contracts."""

    def load_trades(self, valuation_date: date) -> pl.DataFrame:
        """Return trade headers active on ``valuation_date``."""
        ...

    def load_legs(self, valuation_date: date) -> pl.DataFrame:
        """Return the legs that belong to the active trades."""
        ...
