"""Pydantic data contracts for the shared market-risk data kernel.

Contracts govern types and enumerations only; ``risk-data-core`` performs no
mathematical discounting or valuation. Downstream packages use these contracts
to validate inputs before persisting to the ``dim_*`` / ``fct_*`` tables.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DayCountConvention = Literal["ACT/360", "ACT/365", "30/360"]
CompoundingConvention = Literal["CONTINUOUS", "SIMPLE", "COMPOUND"]
LegType = Literal["ACTIVE", "PASSIVE"]
Currency = Literal["CLP", "UF", "USD"]
RateType = Literal["FIXED", "FLOAT"]
Book = Literal["TRADING", "BANKING"]
InstrumentType = Literal["SWAP", "BOND", "FORWARD", "SPOT"]


class TradeContract(BaseModel):
    """Header contract for a trade (swap, bond, forward, or spot position)."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    instrument_type: InstrumentType
    book: Book
    trade_date: date
    maturity_date: date | None = None
    counterparty: str | None = None


class LegContract(BaseModel):
    """Contract for a single trade leg."""

    model_config = ConfigDict(frozen=True)

    leg_id: str
    trade_id: str
    leg_type: LegType
    currency: Currency
    rate_type: RateType
    day_count_convention: DayCountConvention
    compounding_convention: CompoundingConvention
    coupon_rate: float = Field(ge=0.0)
    reference_index: str | None = None


class MarketCurveContract(BaseModel):
    """Contract for a zero-coupon curve node."""

    model_config = ConfigDict(frozen=True)

    valuation_date: date
    curve_name: str
    tenor_label: str
    tenor_yf: float = Field(ge=0.0)
    zero_rate: float
    source: str


class FxRateContract(BaseModel):
    """Contract for an exchange-rate / UF observation."""

    model_config = ConfigDict(frozen=True)

    valuation_date: date
    currency_pair: str
    spot_rate: float = Field(gt=0.0)
    source: str


class ValuedCashflowContract(BaseModel):
    """Contract for an expanded and valued cashflow."""

    model_config = ConfigDict(frozen=True)

    valuation_date: date
    cashflow_id: str
    trade_id: str
    leg_id: str
    payment_date: date
    yf: float = Field(ge=0.0)
    nominal_amount: float
    coupon_rate: float = Field(ge=0.0)
    projected_rate: float
    total_cashflow: float
    discount_factor: float = Field(gt=0.0, le=1.0)
    present_value_orig_ccy: float
    present_value_clp: float
    dv01_orig_ccy: float
    dv01_clp: float
    modified_duration: float


class ValuedCashflowRelaxedContract(ValuedCashflowContract):
    """Same as ``ValuedCashflowContract`` but allows negative coupon rates.

    Reserved for documented extreme-monetary-policy regimes, mirroring
    ``ValuedCashflowRelaxedSchema`` in :mod:`risk_data_core.validation`.
    """

    coupon_rate: float


__all__ = [
    "Book",
    "CompoundingConvention",
    "Currency",
    "DayCountConvention",
    "FxRateContract",
    "InstrumentType",
    "LegContract",
    "LegType",
    "MarketCurveContract",
    "RateType",
    "TradeContract",
    "ValuedCashflowContract",
    "ValuedCashflowRelaxedContract",
]
