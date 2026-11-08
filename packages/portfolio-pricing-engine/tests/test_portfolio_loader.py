from datetime import date
from pathlib import Path

import polars as pl
from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve
from portfolio_pricing_engine.domain.portfolio_loader import PortfolioLoader
from risk_data_core import RiskDatabase


class _FakeTradeRepository:
    def __init__(self, trades: pl.DataFrame, legs: pl.DataFrame) -> None:
        self._trades = trades
        self._legs = legs

    def load_trades(self, valuation_date: date) -> pl.DataFrame:
        return self._trades

    def load_legs(self, valuation_date: date) -> pl.DataFrame:
        return self._legs


class _FakeCurveProvider:
    def fetch_par_rates(self, valuation_date: date, curve_name: str) -> pl.DataFrame:
        return pl.DataFrame({"tenor_yf": [1.0, 2.0], "par_rate": [0.05, 0.05]})


def _trades() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": ["t1"],
            "instrument_type": ["BOND"],
            "book": ["BANKING"],
            "trade_date": [date(2025, 1, 2)],
            "maturity_date": [date(2027, 1, 2)],
            "counterparty": [None],
        }
    )


def _legs() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "leg_id": ["l1"],
            "trade_id": ["t1"],
            "leg_type": ["ACTIVE"],
            "currency": ["CLP"],
            "notional": [100.0],
            "rate_type": ["FIXED"],
            "day_count_convention": ["ACT/365"],
            "compounding_convention": ["CONTINUOUS"],
            "coupon_rate": [0.05],
            "payment_frequency": ["ANNUAL"],
            "reference_index": [None],
        }
    )


def test_portfolio_loader_persists_valued_cashflows_idempotently() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        loader = PortfolioLoader(
            db,
            _FakeTradeRepository(_trades(), _legs()),
            _FakeCurveProvider(),
        )

        first = loader.run_eod(date(2025, 1, 2))
        second = loader.run_eod(date(2025, 1, 2))

        assert first.equals(second)
        stored = db.query_polars("SELECT * FROM fct_valued_cashflows ORDER BY cashflow_id")
        assert stored.height == first.height
    finally:
        db.close()


def test_portfolio_loader_uses_injected_curve_and_conversion() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        loader = PortfolioLoader(db, _FakeTradeRepository(_trades(), _legs()))
        curve = ZeroCurve.from_par_rates(
            pl.DataFrame({"tenor_yf": [1.0, 2.0], "par_rate": [0.05, 0.05]}),
            date(2025, 1, 2),
        )

        valued = loader.run_eod(
            date(2025, 1, 2),
            curve=curve,
            conversion_factors={"CLP": 1.0, "UF": 40_000.0, "USD": 900.0},
        )

        # CLP leg: conversion factor is 1.0.
        assert valued["present_value_clp"].to_list() == valued["present_value_orig_ccy"].to_list()
        assert db.query_polars("SELECT COUNT(*) AS n FROM fct_valued_cashflows")["n"][0] == 2
    finally:
        db.close()
