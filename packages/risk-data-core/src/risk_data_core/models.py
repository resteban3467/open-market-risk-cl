"""SQLAlchemy 2.0 declarative models for the shared market-risk data kernel.

These models are the single source of truth for DDL and metadata governance
only. Bulk reads/writes of analytical data (``fct_*``) are performed with
DuckDB native + Polars via :class:`risk_data_core.database.RiskDatabase`, never
by iterating ORM objects.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Double, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by all data-contract models."""


class Trade(Base):
    """Trade header (swaps, bonds, forwards, Alpaca spot positions)."""

    __tablename__ = "dim_trades"

    trade_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_type: Mapped[str] = mapped_column(String)
    book: Mapped[str] = mapped_column(String)
    trade_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    counterparty: Mapped[str | None] = mapped_column(String)


class Leg(Base):
    """Cashflow leg of a trade (ACTIVE/PASSIVE, currency, rate conventions)."""

    __tablename__ = "dim_legs"

    leg_id: Mapped[str] = mapped_column(String, primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("dim_trades.trade_id"))
    leg_type: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    notional: Mapped[float] = mapped_column(Double)
    rate_type: Mapped[str] = mapped_column(String)
    day_count_convention: Mapped[str] = mapped_column(String)
    compounding_convention: Mapped[str] = mapped_column(String)
    coupon_rate: Mapped[float] = mapped_column(Double)
    payment_frequency: Mapped[str] = mapped_column(String)
    reference_index: Mapped[str | None] = mapped_column(String)


class MarketCurve(Base):
    """Zero-coupon curve nodes per (valuation_date, curve, tenor)."""

    __tablename__ = "fct_market_curves"

    valuation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    curve_name: Mapped[str] = mapped_column(String, primary_key=True)
    tenor_label: Mapped[str] = mapped_column(String, primary_key=True)
    tenor_yf: Mapped[float] = mapped_column(Double)
    zero_rate: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String)


class StgMarketCurve(Base):
    """Raw par/swap-rate curve nodes per (valuation_date, curve, tenor).

    Stores the source observations before zero-coupon bootstrapping; the
    bootstrapped zeros are persisted in ``fct_market_curves``.
    """

    __tablename__ = "stg_market_curves"

    valuation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    curve_name: Mapped[str] = mapped_column(String, primary_key=True)
    tenor_label: Mapped[str] = mapped_column(String, primary_key=True)
    tenor_yf: Mapped[float] = mapped_column(Double)
    par_rate: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String)


class FxRate(Base):
    """Exchange rate / UF history per (valuation_date, currency_pair)."""

    __tablename__ = "fct_fx_rates"

    valuation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency_pair: Mapped[str] = mapped_column(String, primary_key=True)
    spot_rate: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String)


class StgMarketFactor(Base):
    """Macro/credit factor observation at its native frequency."""

    __tablename__ = "stg_market_factors"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    factor_code: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String)
    frequency: Mapped[str] = mapped_column(String)


class MarketFactor(Base):
    """Daily-grain macro/credit factor (native frequency forward-filled)."""

    __tablename__ = "fct_market_factors"

    valuation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    factor_code: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[float] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String)


class BankingVolume(Base):
    """Banking balance (deposits / loans) observation at native frequency."""

    __tablename__ = "fct_banking_volumes"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_code: Mapped[str] = mapped_column(String, primary_key=True)
    amount: Mapped[float] = mapped_column(Double)
    currency: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    frequency: Mapped[str] = mapped_column(String)


class ValuedCashflow(Base):
    """Analytical table of expanded and valued cashflows for R07/EWMA mapping."""

    __tablename__ = "fct_valued_cashflows"

    valuation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cashflow_id: Mapped[str] = mapped_column(String, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String)
    leg_id: Mapped[str] = mapped_column(String)
    payment_date: Mapped[date] = mapped_column(Date)
    yf: Mapped[float] = mapped_column(Double)
    nominal_amount: Mapped[float] = mapped_column(Double)
    coupon_rate: Mapped[float] = mapped_column(Double)
    projected_rate: Mapped[float] = mapped_column(Double)
    total_cashflow: Mapped[float] = mapped_column(Double)
    discount_factor: Mapped[float] = mapped_column(Double)
    present_value_orig_ccy: Mapped[float] = mapped_column(Double)
    present_value_clp: Mapped[float] = mapped_column(Double)
    dv01_orig_ccy: Mapped[float] = mapped_column(Double)
    dv01_clp: Mapped[float] = mapped_column(Double)
    modified_duration: Mapped[float] = mapped_column(Double)


__all__ = [
    "BankingVolume",
    "Base",
    "FxRate",
    "Leg",
    "MarketCurve",
    "MarketFactor",
    "StgMarketCurve",
    "StgMarketFactor",
    "Trade",
    "ValuedCashflow",
]
