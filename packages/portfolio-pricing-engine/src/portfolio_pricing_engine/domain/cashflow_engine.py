"""Vectorized cashflow expansion, present value and DV01.

Turns ``dim_trades`` / ``dim_legs`` rows into dated cashflows and discounts them
with one (or several) :class:`~portfolio_pricing_engine.domain.bootstrapping.ZeroCurve`.
The valuation math uses Polars expressions (no per-row Python loops).

Supported products:

- ``BOND`` (fixed or floating rate note): coupon series + principal at maturity.
- ``SWAP`` (IRS, CLF/CLP, cross-currency): interest-only coupon series per leg.
- ``FORWARD`` / ``SPOT``: a single bullet at maturity.

Floating legs project the next coupon at ``floating_rates[index] + coupon_rate``
(``coupon_rate`` acts as the spread over the reference index). This is a flat
projection at the current index level (documented simplification).
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


def _fraction_expr(start: pl.Expr, end: pl.Expr) -> pl.Expr:
    """Day-count fraction between two date expressions (``start`` -> ``end``)."""
    days = (end - start).dt.total_days()
    act_365 = days / 365.0
    act_360 = days / 360.0
    thirty_360 = (
        (end.dt.year() - start.dt.year()) * 360
        + (end.dt.month() - start.dt.month()) * 30
        + (
            pl.min_horizontal(end.dt.day(), pl.lit(30))
            - pl.min_horizontal(start.dt.day(), pl.lit(30))
        )
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


def _year_fraction_expr(valuation_date: date) -> pl.Expr:
    """Fraction from ``valuation_date`` to ``payment_date`` (discounting axis)."""
    return _fraction_expr(pl.lit(valuation_date).cast(pl.Date), pl.col("payment_date"))


def _period_fraction_expr(start: str, end: str) -> pl.Expr:
    """Fraction of an accrual period between two date columns (coupon accrual)."""
    return _fraction_expr(pl.col(start), pl.col(end))


def _effective_rate_expr(floating_rates: Mapping[str, float] | None) -> pl.Expr:
    """Coupon rate used per cashflow.

    Fixed legs use ``coupon_rate``; floating legs use
    ``coupon_rate`` (spread) + the current level of their reference index.
    """
    index_rate = pl.lit(0.0)
    for code, value in (floating_rates or {}).items():
        index_rate = (
            pl.when(
                (pl.col("rate_type").str.to_uppercase() == "FLOAT")
                & (pl.col("reference_index").str.to_uppercase() == code.upper())
            )
            .then(pl.lit(float(value)))
            .otherwise(index_rate)
        )
    return (
        pl.when(pl.col("rate_type").str.to_uppercase() == "FLOAT")
        .then(pl.col("coupon_rate") + index_rate)
        .otherwise(pl.col("coupon_rate"))
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
            "dv01_orig_ccy": pl.Float64,
            "present_value_clp": pl.Float64,
            "dv01_clp": pl.Float64,
            "modified_duration": pl.Float64,
        }
    )


def _expand_coupon_legs(
    df: pl.DataFrame,
    valuation_date: date,
    effective_rate: pl.Expr,
    *,
    include_principal: bool,
) -> pl.DataFrame:
    """Expand BOND/SWAP legs into coupon cashflows on their payment grid."""
    frames: list[pl.DataFrame] = []
    for frequency, interval in FREQUENCY_INTERVAL.items():
        subset = df.filter(pl.col("payment_frequency") == frequency)
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
            ).explode("payment_date")
        )

    if frames:
        expanded = pl.concat(frames, how="vertical")
    else:
        expanded = df.head(0).with_columns(payment_date=pl.Series([], dtype=pl.Date))

    principal = (
        pl.when(pl.col("is_final")).then(pl.col("notional")).otherwise(pl.lit(0.0))
        if include_principal
        else pl.lit(0.0)
    )

    return (
        expanded.sort(["trade_id", "leg_id", "payment_date"])
        .with_columns(
            accrual_start=pl.col("payment_date")
            .shift(1)
            .over(["trade_id", "leg_id"])
            .fill_null(pl.col("trade_date"))
        )
        .filter(pl.col("payment_date") >= pl.lit(valuation_date))
        .with_columns(
            is_final=pl.col("payment_date") == pl.col("maturity_date"),
            accrual_alpha=_period_fraction_expr("accrual_start", "payment_date"),
            effective_rate=effective_rate,
        )
        .with_columns(
            total_cashflow=pl.col("notional") * pl.col("effective_rate") * pl.col("accrual_alpha")
            + principal,
            projected_rate=pl.col("effective_rate"),
        )
        .select(_BASE_COLUMNS)
    )


def expand_cashflows(
    trades: pl.DataFrame,
    legs: pl.DataFrame,
    valuation_date: date,
    *,
    floating_rates: Mapping[str, float] | None = None,
) -> pl.DataFrame:
    """Expand active BOND/SWAP/FORWARD/SPOT trades into dated cashflows.

    ``floating_rates`` maps a reference index (``ICP``, ``SOFR``, ``UF``, ``TIB``,
    ...) to its current decimal rate; floating coupons project at
    ``floating_rates[index] + leg spread``.
    """
    active = trades.filter(
        (pl.col("trade_date") <= pl.lit(valuation_date))
        & pl.col("maturity_date").is_not_null()
        & (pl.col("maturity_date") >= pl.lit(valuation_date))
    ).select(["trade_id", "instrument_type", "maturity_date", "trade_date"])

    joined = legs.join(active, on="trade_id", how="inner")
    if joined.is_empty():
        return _empty_expanded_frame()

    bonds = joined.filter(pl.col("instrument_type") == "BOND")
    swaps = joined.filter(pl.col("instrument_type") == "SWAP")
    bullets = joined.filter(pl.col("instrument_type").is_in(["FORWARD", "SPOT"]))

    effective_rate = _effective_rate_expr(floating_rates)
    coupon_frames: list[pl.DataFrame] = []
    if not bonds.is_empty():
        coupon_frames.append(
            _expand_coupon_legs(bonds, valuation_date, effective_rate, include_principal=True)
        )
    if not swaps.is_empty():
        coupon_frames.append(
            _expand_coupon_legs(swaps, valuation_date, effective_rate, include_principal=False)
        )

    bullets_expanded = bullets.with_columns(
        payment_date=pl.col("maturity_date"),
        total_cashflow=pl.col("notional"),
        projected_rate=pl.lit(0.0),
    ).select(_BASE_COLUMNS)

    frames = [*coupon_frames, bullets_expanded]
    cashflows = pl.concat(frames, how="vertical")
    return cashflows.with_columns(
        valuation_date=pl.lit(valuation_date),
        nominal_amount=pl.col("notional"),
        yf=_year_fraction_expr(valuation_date),
        cashflow_id=pl.concat_str(
            ["trade_id", "leg_id", pl.col("payment_date").cast(pl.String)],
            separator=":",
        ),
    ).select(_EXPANDED_COLUMNS)


def _discount_by_currency(
    cashflows: pl.DataFrame,
    curves: Mapping[str, ZeroCurve],
    fallback: ZeroCurve | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Discount factors (base, +1bp, -1bp) using each row's currency curve."""
    yf = cashflows["yf"].to_numpy().astype(np.float64)
    currencies = cashflows["currency"].to_numpy()
    discount = np.empty_like(yf)
    discount_up = np.empty_like(yf)
    discount_dn = np.empty_like(yf)
    for currency in np.unique(currencies):
        mask = currencies == currency
        curve = curves.get(str(currency), fallback)
        if curve is None:
            msg = f"no curve for currency: {currency!r}"
            raise ValueError(msg)
        discount[mask] = curve.discount_factor(yf[mask])
        discount_up[mask] = curve.discount_factor(yf[mask], bump=0.0001)
        discount_dn[mask] = curve.discount_factor(yf[mask], bump=-0.0001)
    return discount, discount_up, discount_dn


def compute_pv_dv01(
    cashflows: pl.DataFrame,
    curve: ZeroCurve | None = None,
    conversion_factors: Mapping[str, float] | None = None,
    *,
    curves: Mapping[str, ZeroCurve] | None = None,
) -> pl.DataFrame:
    """Discount cashflows and compute PV and DV01 via a +/-1bp bump.

    ``curves`` maps a leg currency to its discount curve (used for
    cross-currency products); ``curve`` is the single-curve fallback and is also
    used for any currency missing from ``curves``. ``conversion_factors`` maps a
    leg currency to its CLP factor on the valuation date.
    """
    if cashflows.is_empty():
        return _empty_valued_frame()
    if curves is not None:
        discount, discount_up, discount_dn = _discount_by_currency(cashflows, curves, curve)
    elif curve is not None:
        yf = cashflows["yf"].to_numpy().astype(np.float64)
        discount = curve.discount_factor(yf)
        discount_up = curve.discount_factor(yf, bump=0.0001)
        discount_dn = curve.discount_factor(yf, bump=-0.0001)
    else:
        msg = "compute_pv_dv01 requires either `curve` or `curves`"
        raise ValueError(msg)

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
