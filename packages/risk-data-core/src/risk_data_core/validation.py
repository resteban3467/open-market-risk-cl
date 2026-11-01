"""Runtime ingestion contracts for the shared market-risk data kernel.

Every DataFrame written through
:meth:`risk_data_core.database.RiskDatabase.bulk_insert_polars` is validated
against a :mod:`pandera` contract *before* any SQL touches DuckDB. This enforces
the ``data-resilience-standards`` skill: strict dtypes, prohibited nulls, and
logical ranges (for example ``discount_factor`` strictly in ``(0, 1]``).

The contracts mirror the SQLAlchemy tables in :mod:`risk_data_core.models` and
perform no valuation math.
"""

import pandera.polars as pa
import polars as pl

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

SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "fct_market_curves": MarketCurveSchema,
    "fct_fx_rates": FxRateSchema,
    "fct_valued_cashflows": ValuedCashflowSchema,
}


def validate_ingestion(
    df: pl.DataFrame,
    table_name: str,
    *,
    allow_negative_coupon: bool = False,
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

    if allow_negative_coupon and table_name == "fct_valued_cashflows":
        schema = ValuedCashflowRelaxedSchema

    return schema.validate(df)


__all__ = [
    "SCHEMAS",
    "FxRateSchema",
    "MarketCurveSchema",
    "ValuedCashflowRelaxedSchema",
    "ValuedCashflowSchema",
    "validate_ingestion",
]
