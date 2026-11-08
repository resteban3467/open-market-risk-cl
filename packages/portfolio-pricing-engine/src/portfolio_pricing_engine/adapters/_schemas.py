"""Pandera contracts local to the market-data adapters."""

import pandera.polars as pa
import polars as pl

ParRateSchema = pa.DataFrameSchema(
    {
        "tenor_label": pa.Column(str),
        "tenor_yf": pa.Column(pl.Float64, pa.Check.ge(0.0)),
        "par_rate": pa.Column(pl.Float64),
    },
    strict=True,
    coerce=False,
)

EquityPriceSchema = pa.DataFrameSchema(
    {
        "symbol": pa.Column(str),
        "price": pa.Column(pl.Float64, pa.Check.gt(0.0)),
        "currency": pa.Column(str),
    },
    strict=True,
    coerce=False,
)

__all__ = ["EquityPriceSchema", "ParRateSchema"]
