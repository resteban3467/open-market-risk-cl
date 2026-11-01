from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from pandera.errors import SchemaError
from risk_data_core.validation import (
    SCHEMAS,
    FxRateSchema,
    MarketCurveSchema,
    ValuedCashflowRelaxedSchema,
    ValuedCashflowSchema,
    validate_ingestion,
)


def _fx_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "currency_pair": ["USD/CLP"],
            "spot_rate": [900.25],
            "source": ["test"],
        }
    )


def _curve_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "curve_name": ["CLP_ZERO"],
            "tenor_label": ["1Y"],
            "tenor_yf": [1.0],
            "zero_rate": [-0.0025],
            "source": ["BCCh"],
        }
    )


def _cashflow_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "cashflow_id": ["cf-1"],
            "trade_id": ["trade-1"],
            "leg_id": ["leg-1"],
            "payment_date": [date(2027, 1, 2)],
            "yf": [1.0],
            "nominal_amount": [1_000_000.0],
            "coupon_rate": [0.045],
            "projected_rate": [0.045],
            "total_cashflow": [45_000.0],
            "discount_factor": [0.9560],
            "present_value_orig_ccy": [43_020.0],
            "present_value_clp": [43_020.0],
            "dv01_orig_ccy": [-0.41],
            "dv01_clp": [-0.41],
            "modified_duration": [0.95],
        }
    )


def test_registry_exposes_one_contract_per_analytical_table() -> None:
    assert set(SCHEMAS) == {"fct_market_curves", "fct_fx_rates", "fct_valued_cashflows"}
    assert SCHEMAS["fct_market_curves"] is MarketCurveSchema
    assert SCHEMAS["fct_fx_rates"] is FxRateSchema
    assert SCHEMAS["fct_valued_cashflows"] is ValuedCashflowSchema
    assert ValuedCashflowRelaxedSchema is not ValuedCashflowSchema
    assert not ValuedCashflowRelaxedSchema.columns["coupon_rate"].checks


def test_valid_frames_pass_unchanged() -> None:
    for table_name, frame in (
        ("fct_market_curves", _curve_frame()),
        ("fct_fx_rates", _fx_frame()),
        ("fct_valued_cashflows", _cashflow_frame()),
    ):
        result = validate_ingestion(frame, table_name)
        assert result.to_dicts() == frame.to_dicts()
        assert result.schema == frame.schema


def test_discount_factor_must_be_strictly_between_zero_and_one() -> None:
    base = _cashflow_frame()
    for bad in (0.0, -0.01, 1.0001):
        broken = base.with_columns(pl.lit(bad).alias("discount_factor"))
        with pytest.raises(SchemaError):
            validate_ingestion(broken, "fct_valued_cashflows")


def test_coupon_rate_rejects_negative_values_by_default() -> None:
    broken = _cashflow_frame().with_columns(pl.lit(-0.0025).alias("coupon_rate"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_valued_cashflows")


def test_negative_coupon_is_allowed_only_with_explicit_opt_in() -> None:
    relaxed = _cashflow_frame().with_columns(pl.lit(-0.0025).alias("coupon_rate"))
    result = validate_ingestion(
        relaxed,
        "fct_valued_cashflows",
        allow_negative_coupon=True,
    )
    assert result["coupon_rate"].to_list() == [-0.0025]


def test_spot_rate_must_be_strictly_positive() -> None:
    for bad in (0.0, -1.0):
        broken = _fx_frame().with_columns(pl.lit(bad).alias("spot_rate"))
        with pytest.raises(SchemaError):
            validate_ingestion(broken, "fct_fx_rates")


def test_prohibited_nulls_are_rejected() -> None:
    broken = _fx_frame().with_columns(pl.lit(None).cast(pl.Float64).alias("spot_rate"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_fx_rates")


def test_negative_zero_rate_is_allowed() -> None:
    result = validate_ingestion(_curve_frame(), "fct_market_curves")
    assert result["zero_rate"].to_list() == [-0.0025]


def test_tenor_year_fraction_must_be_non_negative() -> None:
    broken = _curve_frame().with_columns(pl.lit(-0.5).alias("tenor_yf"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_market_curves")


def test_unknown_table_has_no_contract() -> None:
    with pytest.raises(ValueError, match="no ingestion contract"):
        validate_ingestion(_fx_frame(), "fct_unknown")


def test_unexpected_columns_are_rejected() -> None:
    broken = _fx_frame().with_columns(pl.lit(1.0).alias("unexpected"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_fx_rates")
