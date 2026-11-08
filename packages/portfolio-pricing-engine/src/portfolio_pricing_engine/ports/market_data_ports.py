"""Ports for external market-data sources.

Adapters (BCCh, FRED, Alpaca) implement these protocols and return *pure*
Polars frames validated by Pandera; the domain never sees HTTP details.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import polars as pl


class YieldCurvePort(Protocol):
    """Source of par-rate observations used to bootstrap zero curves."""

    def fetch_par_rates(self, valuation_date: date, curve_name: str) -> pl.DataFrame:
        """Return ``tenor_label``, ``tenor_yf`` and ``par_rate`` (decimal) rows."""
        ...


class FXRatePort(Protocol):
    """Source of exchange-rate observations."""

    def fetch_fx(self, valuation_date: date, currency_pair: str) -> pl.DataFrame:
        """Return a ``fct_fx_rates``-shaped frame (``spot_rate`` in decimal)."""
        ...


class SeriesHistoryPort(Protocol):
    """Source of full-history observations used by the bulk ingestion engine.

    Parameters are positional-only so that adapters may use their own parameter
    names (``code`` vs ``series_id``) while still satisfying the protocol.
    """

    def fetch_series_history(self, code: str, start: date, end: date, /) -> pl.DataFrame:
        """Return ``(as_of_date, value)`` rows for a single series over a range."""
        ...

    def fetch_curve_history(
        self,
        codes: dict[str, str],
        start: date,
        end: date,
        /,
    ) -> pl.DataFrame:
        """Return par-rate history for a curve: ``(valuation_date, tenor_label, ...)``."""
        ...


class EquityPricePort(Protocol):
    """Source of equity / ETF price observations."""

    def fetch_price(self, valuation_date: date, symbol: str) -> pl.DataFrame:
        """Return ``symbol``, ``price`` and ``currency`` rows."""
        ...
