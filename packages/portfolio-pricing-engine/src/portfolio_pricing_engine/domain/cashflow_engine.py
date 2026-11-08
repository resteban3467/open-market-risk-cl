"""Vectorized cashflow expansion, present value and DV01.

Turns ``dim_trades`` / ``dim_legs`` rows into dated coupon cashflows and
discounts them with a :class:`~portfolio_pricing_engine.domain.bootstrapping.ZeroCurve`.
The valuation math uses Polars expressions (no per-row Python loops).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np
import polars as pl

from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve

FREQUENCY_INTERVAL: dict[str, str] = {
    "ANNUAL": "1y",
    "SEMIANNUAL": "6mo",
    "QUARTERLY": "3mo",
}

_EXPANDED_COLUMNS = [
    "valuation_date",
    "cashflow_id",
    "trade_id",
    "leg_id",
    "currency",
    "payment_date",
    "yf",
    "nominal_amount",
    "coupon_rate",
    "projected_rate",
    "total_cashflow",
]

_VALUED_COLUMNS = [
    "valuation_date",
    "cashflow_id",
    "trade_id",
    "leg_id",
    "payment_date",
    "yf",
    "nominal_amount",
    "coupon_rate",
    "projected_rate",
    "total_cashflow",
    "discount_factor",
    "present_value_orig_ccy",
    "present_value_clp",
    "dv01_orig_ccy",
    "dv01_clp",
    "modified_duration",
]

_BASE_COLUMNS = [
    "trade_id",
    "leg_id",
    "currency",
    "payment_date",
    "notional",
    "coupon_rate",
    "total_cashflow",
    "projected_rate",
    "day_count_convention",
]


def _year_fraction_expr(valuation_date: date) -> pl.Expr:
    days = (pl.col("payment_date") - pl.lit(valuation_date)).dt.total_days()
    act_365 = days / 365.0
    act_360 = days / 360.0
    end_year = pl.col("payment_date").dt.year()
    end_month = pl.col("payment_date").dt.month()
    end_day = pl.col("payment_date").dt.day()
    thirty_360 = (
        (end_year - pl.lit(valuation_date.year)) * 360
        + (end_month - pl.lit(valuation_date.month)) * 30
        + (pl.min_horizontal(end_day, 30) - pl.lit(min(valuation_date.day, 30)))
    ) / 360.0
    return (
        pl.when(pl.col("day_count_convention") == "ACT/365")
        .then(act_365)
        .when(pl.col("day_count_convention") == "ACT/360")
        .then(act_360)
        .when(pl.col("day_count_convention") == "30/360")
        .then(thirty_360)
        .otherwise(act_365)
    )


def _empty_expanded_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "valuation_date": pl.Date,
            "cashflow_id": pl.String,
            "trade_id": pl.String,
            "leg_id": pl.String,
            "currency": pl.String,
            "payment_date": pl.Date,
            "yf": pl.Float64,
            "nominal_amount": pl.Float64,
            "coupon_rate": pl.Float64,
            "projected_rate": pl.Float64,
            "total_cashflow": pl.Float64,
        }
    )


def _empty_valued_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "valuation_date": pl.Date,
            "cashflow_id": pl.String,
            "trade_id": pl.String,
            "leg_id": pl.String,
            "payment_date": pl.Date,
            "yf": pl.Float64,
            "nominal_amount": pl.Float64,
            "coupon_rate": pl.Float64,
            "projected_rate": pl.Float64,
            "total_cashflow": pl.Float64,
            "discount_factor": pl.Float64,
            "present_value_orig_ccy": pl.Float64,
            "present_value_clp": pl.Float64,
            "dv01_orig_ccy": pl.Float64,
            "dv01_clp": pl.Float64,
            "modified_duration": pl.Float64,
        }
    )


def expand_cashflows(
    trades: pl.DataFrame,
    legs: pl.DataFrame,
    valuation_date: date,
) -> pl.DataFrame:
    """Expand fixed-rate BOND/FORWARD/SPOT trades into dated cashflows."""
    active = trades.filter(
        (pl.col("trade_date") <= pl.lit(valuation_date))
        & pl.col("maturity_date").is_not_null()
        & (pl.col("maturity_date") >= pl.lit(valuation_date))
    ).select(["trade_id", "instrument_type", "maturity_date", "trade_date"])

    joined = legs.join(active, on="trade_id", how="inner").filter(pl.col("rate_type") == "FIXED")
    if joined.is_empty():
        return _empty_expanded_frame()

    bonds = joined.filter(pl.col("instrument_type") == "BOND")
    bullets = joined.filter(pl.col("instrument_type").is_in(["FORWARD", "SPOT"]))

    frames: list[pl.DataFrame] = []
    for frequency, interval in FREQUENCY_INTERVAL.items():
        subset = bonds.filter(pl.col("payment_frequency") == frequency)
        if subset.is_empty():
            continue
        frames.append(
            subset.with_columns(
                payment_date=pl.date_ranges(
                    pl.col("trade_date"),
                    pl.col("maturity_date"),
                    interval=interval,
                    closed="right",
                )
            )
            .explode("payment_date")
            .filter(pl.col("payment_date") >= pl.lit(valuation_date))
        )

    if frames:
        bonds_expanded = pl.concat(frames, how="vertical")
    else:
        bonds_expanded = bonds.head(0).with_columns(payment_date=pl.Series([], dtype=pl.Date))

    bonds_expanded = (
        bonds_expanded.with_columns(
            period_count=pl.when(pl.col("payment_frequency") == "ANNUAL")
            .then(pl.lit(1.0))
            .when(pl.col("payment_frequency") == "SEMIANNUAL")
            .then(pl.lit(2.0))
            .otherwise(pl.lit(4.0)),
            is_final=pl.col("payment_date") == pl.col("maturity_date"),
        )
        .with_columns(
            total_cashflow=pl.col("notional") * pl.col("coupon_rate") / pl.col("period_count")
            + pl.when(pl.col("is_final")).then(pl.col("notional")).otherwise(pl.lit(0.0)),
            projected_rate=pl.col("coupon_rate"),
        )
        .select(_BASE_COLUMNS)
    )

    bullets_expanded = bullets.with_columns(
        payment_date=pl.col("maturity_date"),
        total_cashflow=pl.col("notional"),
        projected_rate=pl.lit(0.0),
    ).select(_BASE_COLUMNS)

    cashflows = pl.concat([bonds_expanded, bullets_expanded], how="vertical")
    return cashflows.with_columns(
        valuation_date=pl.lit(valuation_date),
        nominal_amount=pl.col("notional"),
        yf=_year_fraction_expr(valuation_date),
        cashflow_id=pl.concat_str(
            ["trade_id", "leg_id", pl.col("payment_date").cast(pl.String)],
            separator=":",
        ),
    ).select(_EXPANDED_COLUMNS)


def compute_pv_dv01(
    cashflows: pl.DataFrame,
    curve: ZeroCurve,
    conversion_factors: Mapping[str, float] | None = None,
) -> pl.DataFrame:
    """Discount cashflows and compute PV and DV01 via a +/-1bp bump.

    ``conversion_factors`` maps a leg currency to its CLP factor on the
    valuation date (``{"CLP": 1.0, "UF": uf_value, "USD": usd_clp}``). When
    omitted, ``present_value_clp``/``dv01_clp`` equal the original-currency
    amounts (factor ``1.0``).
    """
    if cashflows.is_empty():
        return _empty_valued_frame()
    yf = cashflows["yf"].to_numpy().astype(np.float64)
    discount = curve.discount_factor(yf)
    discount_up = curve.discount_factor(yf, bump=0.0001)
    discount_dn = curve.discount_factor(yf, bump=-0.0001)

    factor_expr = pl.lit(1.0)
    if conversion_factors and "currency" in cashflows.columns:
        present = set(cashflows["currency"].unique().to_list())
        missing = present - set(conversion_factors)
        if missing:
            msg = f"missing conversion factor for currencies: {sorted(missing)}"
            raise ValueError(msg)
        for currency, factor in conversion_factors.items():
            factor_expr = (
                pl.when(pl.col("currency") == currency).then(pl.lit(factor)).otherwise(factor_expr)
            )

    return (
        cashflows.with_columns(
            pl.Series("discount_factor", discount, dtype=pl.Float64),
            pl.Series("_discount_up", discount_up, dtype=pl.Float64),
            pl.Series("_discount_dn", discount_dn, dtype=pl.Float64),
        )
        .with_columns(
            present_value_orig_ccy=pl.col("total_cashflow") * pl.col("discount_factor"),
            dv01_orig_ccy=-(
                pl.col("total_cashflow") * (pl.col("_discount_up") - pl.col("_discount_dn")) / 2.0
            ),
            _fx_factor=factor_expr,
        )
        .with_columns(
            present_value_clp=pl.col("present_value_orig_ccy") * pl.col("_fx_factor"),
            dv01_clp=pl.col("dv01_orig_ccy") * pl.col("_fx_factor"),
            modified_duration=pl.col("yf"),
        )
        .select(_VALUED_COLUMNS)
    )


__all__ = ["compute_pv_dv01", "expand_cashflows"]
