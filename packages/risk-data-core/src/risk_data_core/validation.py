"""Runtime ingestion contracts for the shared market-risk data kernel.

Every DataFrame written through
:meth:`risk_data_core.database.RiskDatabase.bulk_insert_polars` is validated
against a :mod:`pandera` contract *before* any SQL touches DuckDB. This enforces
the ``data-resilience-standards`` skill: strict dtypes, prohibited nulls, and
logical ranges (for example ``discount_factor`` strictly in ``(0, 1]``).

The contracts mirror the SQLAlchemy tables in :mod:`risk_data_core.models` and
perform no valuation math.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandera.polars as pa
import polars as pl

from risk_data_core.observability import get_logger

MarketCurveSchema = pa.DataFrameSchema(
    {
        "valuation_date": pa.Column(pl.Date),
        "curve_name": pa.Column(str),
        "tenor_label": pa.Column(str),
        "tenor_yf": pa.Column(pl.Float64, pa.Check.ge(0.0)),
        "zero_rate": pa.Column(pl.Float64),
        "source": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

FxRateSchema = pa.DataFrameSchema(
    {
        "valuation_date": pa.Column(pl.Date),
        "currency_pair": pa.Column(str),
        "spot_rate": pa.Column(pl.Float64, pa.Check.gt(0.0)),
        "source": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

StgMarketCurveSchema = pa.DataFrameSchema(
    {
        "valuation_date": pa.Column(pl.Date),
        "curve_name": pa.Column(str),
        "tenor_label": pa.Column(str),
        "tenor_yf": pa.Column(pl.Float64, pa.Check.ge(0.0)),
        "par_rate": pa.Column(pl.Float64),
        "source": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

StgMarketFactorSchema = pa.DataFrameSchema(
    {
        "as_of_date": pa.Column(pl.Date),
        "factor_code": pa.Column(str),
        "value": pa.Column(pl.Float64),
        "source": pa.Column(str),
        "frequency": pa.Column(str, pa.Check.isin(["DAILY", "MONTHLY", "QUARTERLY"])),
    },
    strict=True,
    coerce=False,
)

MarketFactorSchema = pa.DataFrameSchema(
    {
        "valuation_date": pa.Column(pl.Date),
        "factor_code": pa.Column(str),
        "value": pa.Column(pl.Float64),
        "source": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

BankingVolumeSchema = pa.DataFrameSchema(
    {
        "as_of_date": pa.Column(pl.Date),
        "instrument_code": pa.Column(str),
        "amount": pa.Column(pl.Float64),
        "currency": pa.Column(str),
        "source": pa.Column(str),
        "frequency": pa.Column(str, pa.Check.isin(["DAILY", "MONTHLY", "QUARTERLY"])),
    },
    strict=True,
    coerce=False,
)

ValuedCashflowSchema = pa.DataFrameSchema(
    {
        "valuation_date": pa.Column(pl.Date),
        "cashflow_id": pa.Column(str),
        "trade_id": pa.Column(str),
        "leg_id": pa.Column(str),
        "payment_date": pa.Column(pl.Date),
        "yf": pa.Column(pl.Float64, pa.Check.ge(0.0)),
        "nominal_amount": pa.Column(pl.Float64),
        "coupon_rate": pa.Column(pl.Float64, pa.Check.ge(0.0)),
        "projected_rate": pa.Column(pl.Float64),
        "total_cashflow": pa.Column(pl.Float64),
        "discount_factor": pa.Column(pl.Float64, [pa.Check.gt(0.0), pa.Check.le(1.0)]),
        "present_value_orig_ccy": pa.Column(pl.Float64),
        "present_value_clp": pa.Column(pl.Float64),
        "dv01_orig_ccy": pa.Column(pl.Float64),
        "dv01_clp": pa.Column(pl.Float64),
        "modified_duration": pa.Column(pl.Float64),
    },
    strict=True,
    coerce=False,
)

# Same contract as ``ValuedCashflowSchema`` but without the ``coupon_rate >= 0``
# check. Reserved for documented extreme-monetary-policy regimes.
ValuedCashflowRelaxedSchema = ValuedCashflowSchema.update_column("coupon_rate", checks=None)

# Real (UF) curves can carry negative real rates, so their discount factors can
# exceed 1.0. This variant widens the upper bound from 1.0 to 2.0.
ValuedCashflowNegativeRatesSchema = ValuedCashflowSchema.update_column(
    "discount_factor",
    checks=[pa.Check.gt(0.0), pa.Check.le(2.0)],
)
ValuedCashflowRelaxedNegativeRatesSchema = ValuedCashflowNegativeRatesSchema.update_column(
    "coupon_rate", checks=None
)

SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "fct_market_curves": MarketCurveSchema,
    "fct_fx_rates": FxRateSchema,
    "fct_valued_cashflows": ValuedCashflowSchema,
    "fct_market_factors": MarketFactorSchema,
    "fct_banking_volumes": BankingVolumeSchema,
    "stg_market_curves": StgMarketCurveSchema,
    "stg_market_factors": StgMarketFactorSchema,
}


def validate_ingestion(
    df: pl.DataFrame,
    table_name: str,
    *,
    allow_negative_coupon: bool = False,
    allow_negative_rates: bool = False,
) -> pl.DataFrame:
    """Validate ``df`` against the ingestion contract for ``table_name``.

    Parameters
    ----------
    df:
        The Polars frame about to be persisted. It is validated, not mutated.
    table_name:
        One of the ``fct_*`` tables registered in :data:`SCHEMAS`.
    allow_negative_coupon:
        When ``True`` and the target is ``fct_valued_cashflows``, use the relaxed
        coupon-rate contract reserved for extreme monetary-policy regimes.
    allow_negative_rates:
        When ``True`` and the target is ``fct_valued_cashflows``, widen the
        discount-factor upper bound to ``2.0`` so real (UF) curves with negative
        real rates (DF > 1) are accepted.

    Returns
    -------
    pl.DataFrame
        The validated frame (unchanged when the contract holds).

    Raises
    ------
    ValueError
        If no contract is registered for ``table_name``.
    pandera.errors.SchemaError
        If the frame violates the contract (dtype, null, range, strict columns).
    """
    try:
        schema = SCHEMAS[table_name]
    except KeyError:
        msg = f"no ingestion contract registered for table {table_name!r}"
        raise ValueError(msg) from None

    if table_name == "fct_valued_cashflows":
        if allow_negative_coupon and allow_negative_rates:
            schema = ValuedCashflowRelaxedNegativeRatesSchema
        elif allow_negative_coupon:
            schema = ValuedCashflowRelaxedSchema
        elif allow_negative_rates:
            schema = ValuedCashflowNegativeRatesSchema

    return schema.validate(df)


CurveRegime = Literal["NOMINAL", "REAL"]

# (warn_forward, min_forward) floors per regime, in decimal (e.g. -0.005 = -50bp).
_CURVE_FORWARD_FLOORS: dict[str, tuple[float, float]] = {
    "NOMINAL": (0.0, -0.005),
    "REAL": (-0.05, -0.08),
}


@dataclass(frozen=True)
class CurveArbitrageResult:
    """Result of a no-arbitrage screen on a zero curve."""

    clean: pl.DataFrame
    quarantined: pl.DataFrame


def validate_curve_no_arbitrage(
    df: pl.DataFrame,
    *,
    regime: CurveRegime = "NOMINAL",
    warn_forward: float | None = None,
    min_forward: float | None = None,
) -> CurveArbitrageResult:
    """Quarantine curve dates whose implied forwards breach the regime floor.

    ``df`` must carry ``valuation_date``, ``curve_name``, ``tenor_yf`` and
    ``zero_rate``. For each ``(curve_name, valuation_date)`` the implied forward
    between adjacent tenors is ``f = (z_i t_i - z_{i-1} t_{i-1}) / (t_i - t_{i-1})``.

    - Green: ``f >= warn_forward`` -> accepted silently.
    - Yellow: ``min_forward <= f < warn_forward`` -> accepted with a warning.
    - Red: ``f < min_forward`` -> the whole ``(curve, date)`` is quarantined.

    Defaults: NOMINAL ``(warn=0.0, min=-0.005)``; REAL ``(warn=-0.05, min=-0.08)``.
    """
    logger = get_logger(__name__)
    try:
        default_warn, default_min = _CURVE_FORWARD_FLOORS[regime]
    except KeyError:
        msg = f"invalid curve regime: {regime!r}"
        raise ValueError(msg) from None
    warn = default_warn if warn_forward is None else warn_forward
    floor = default_min if min_forward is None else min_forward

    if df.is_empty():
        return CurveArbitrageResult(clean=df, quarantined=df)

    group = ["curve_name", "valuation_date"]
    work = df.sort([*group, "tenor_yf"]).with_columns(
        pl.col("tenor_yf").shift(1).over(group).alias("_prev_t"),
        pl.col("zero_rate").shift(1).over(group).alias("_prev_z"),
    )
    work = work.with_columns(
        pl.when(pl.col("_prev_t").is_not_null() & (pl.col("tenor_yf") > pl.col("_prev_t")))
        .then(
            (pl.col("zero_rate") * pl.col("tenor_yf") - pl.col("_prev_z") * pl.col("_prev_t"))
            / (pl.col("tenor_yf") - pl.col("_prev_t"))
        )
        .otherwise(None)
        .alias("_forward")
    )
    work = work.with_columns(
        pl.col("_forward").lt(floor).fill_null(False).any().over(group).alias("_quarantine")
    )

    helper = ["_prev_t", "_prev_z", "_forward", "_quarantine"]
    quarantined = work.filter(pl.col("_quarantine")).drop(helper)
    clean = work.filter(~pl.col("_quarantine")).drop(helper)

    yellow = work.filter(
        pl.col("_forward").is_not_null()
        & (pl.col("_forward") < warn)
        & (pl.col("_forward") >= floor)
    )
    if yellow.height:
        logger.warning(
            "negative_forward_tolerated",
            regime=regime,
            count=yellow.height,
            warn_forward=warn,
        )
    for curve, day in quarantined.select(group).unique().sort(group).iter_rows():
        logger.error(
            "curve_quarantined_arbitrage",
            regime=regime,
            curve_name=curve,
            valuation_date=str(day),
            min_forward=floor,
        )
    return CurveArbitrageResult(clean=clean, quarantined=quarantined)


__all__ = [
    "SCHEMAS",
    "BankingVolumeSchema",
    "CurveArbitrageResult",
    "CurveRegime",
    "FxRateSchema",
    "MarketCurveSchema",
    "MarketFactorSchema",
    "StgMarketCurveSchema",
    "StgMarketFactorSchema",
    "ValuedCashflowNegativeRatesSchema",
    "ValuedCashflowRelaxedNegativeRatesSchema",
    "ValuedCashflowRelaxedSchema",
    "ValuedCashflowSchema",
    "validate_curve_no_arbitrage",
    "validate_ingestion",
]
