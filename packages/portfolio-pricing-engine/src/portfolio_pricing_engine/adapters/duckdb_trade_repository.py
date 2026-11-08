"""DuckDB-backed implementation of :class:`TradeRepositoryPort`."""

from __future__ import annotations

from datetime import date

import polars as pl
from risk_data_core import RiskDatabase


class DuckDBTradeRepository:
    """Read ``dim_trades`` / ``dim_legs`` through the shared kernel."""

    def __init__(self, db: RiskDatabase) -> None:
        self._db = db

    def load_trades(self, valuation_date: date) -> pl.DataFrame:
        return self._db.query_polars(
            "SELECT trade_id, instrument_type, book, trade_date, maturity_date, counterparty "
            "FROM dim_trades "
            "WHERE trade_date <= ? AND (maturity_date IS NULL OR maturity_date >= ?)",
            [valuation_date, valuation_date],
        )

    def load_legs(self, valuation_date: date) -> pl.DataFrame:
        return self._db.query_polars(
            "SELECT leg_id, trade_id, leg_type, currency, notional, rate_type, "
            "day_count_convention, compounding_convention, coupon_rate, "
            "payment_frequency, reference_index FROM dim_legs"
        )


__all__ = ["DuckDBTradeRepository"]
