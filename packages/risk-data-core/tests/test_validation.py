from __future__ import annotations

from datetime import date
from typing import cast

import polars as pl
import pytest
from pandera.errors import SchemaError
from risk_data_core.validation import (
    SCHEMAS,
    BankingVolumeSchema,
    CurveRegime,
    FxRateSchema,
    MarketCurveSchema,
    MarketFactorSchema,
    StgMarketCurveSchema,
    StgMarketFactorSchema,
    ValuedCashflowNegativeRatesSchema,
    ValuedCashflowRelaxedSchema,
    ValuedCashflowSchema,
    validate_curve_no_arbitrage,
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
    assert set(SCHEMAS) == {
        "fct_market_curves",
        "fct_fx_rates",
        "fct_valued_cashflows",
        "fct_market_factors",
        "fct_banking_volumes",
        "stg_market_curves",
        "stg_market_factors",
    }
    assert SCHEMAS["fct_market_curves"] is MarketCurveSchema
    assert SCHEMAS["fct_fx_rates"] is FxRateSchema
    assert SCHEMAS["fct_valued_cashflows"] is ValuedCashflowSchema
    assert SCHEMAS["fct_market_factors"] is MarketFactorSchema
    assert SCHEMAS["fct_banking_volumes"] is BankingVolumeSchema
    assert SCHEMAS["stg_market_curves"] is StgMarketCurveSchema
    assert SCHEMAS["stg_market_factors"] is StgMarketFactorSchema
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


def _stg_curve_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "curve_name": ["CLP_BCP"],
            "tenor_label": ["2Y"],
            "tenor_yf": [2.0],
            "par_rate": [0.05],
            "source": ["BCCh"],
        }
    )


def _stg_factor_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "as_of_date": [date(2026, 1, 2)],
            "factor_code": ["IPC"],
            "value": [105.3],
            "source": ["BCCh"],
            "frequency": ["MONTHLY"],
        }
    )


def _factor_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "factor_code": ["VIX"],
            "value": [18.5],
            "source": ["FRED"],
        }
    )


def test_valid_new_frames_pass_unchanged() -> None:
    for table_name, frame in (
        ("stg_market_curves", _stg_curve_frame()),
        ("stg_market_factors", _stg_factor_frame()),
        ("fct_market_factors", _factor_frame()),
    ):
        result = validate_ingestion(frame, table_name)
        assert result.to_dicts() == frame.to_dicts()


def test_stg_curve_par_rate_rejects_negative_year_fraction() -> None:
    broken = _stg_curve_frame().with_columns(pl.lit(-1.0).alias("tenor_yf"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "stg_market_curves")


def test_stg_factor_rejects_unknown_frequency() -> None:
    broken = _stg_factor_frame().with_columns(pl.lit("WEEKLY").alias("frequency"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "stg_market_factors")


def test_factor_rejects_prohibited_null() -> None:
    broken = _factor_frame().with_columns(pl.lit(None).cast(pl.Float64).alias("value"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_market_factors")


def _volume_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "as_of_date": [date(2026, 1, 2)],
            "instrument_code": ["DEP_CC"],
            "amount": [1_000_000.0],
            "currency": ["CLP"],
            "source": ["BCCh"],
            "frequency": ["DAILY"],
        }
    )


def test_banking_volume_contract_accepts_valid_frame() -> None:
    result = validate_ingestion(_volume_frame(), "fct_banking_volumes")
    assert result.to_dicts() == _volume_frame().to_dicts()


def test_banking_volume_rejects_unknown_frequency() -> None:
    broken = _volume_frame().with_columns(pl.lit("WEEKLY").alias("frequency"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_banking_volumes")


def test_quarterly_factor_frequency_is_accepted() -> None:
    frame = _stg_factor_frame().with_columns(pl.lit("QUARTERLY").alias("frequency"))
    result = validate_ingestion(frame, "stg_market_factors")
    assert result["frequency"].to_list() == ["QUARTERLY"]


def test_unexpected_columns_are_rejected() -> None:
    broken = _fx_frame().with_columns(pl.lit(1.0).alias("unexpected"))
    with pytest.raises(SchemaError):
        validate_ingestion(broken, "fct_fx_rates")


def _zero_frame(z_by_t: dict[float, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)] * len(z_by_t),
            "curve_name": ["CLP_TEST"] * len(z_by_t),
            "tenor_label": [f"{int(t)}Y" for t in z_by_t],
            "tenor_yf": list(z_by_t.keys()),
            "zero_rate": list(z_by_t.values()),
            "source": ["test"] * len(z_by_t),
        }
    )


def test_no_arbitrage_accepts_positive_forwards() -> None:
    result = validate_curve_no_arbitrage(
        _zero_frame({1.0: 0.05, 2.0: 0.055}),
        regime="NOMINAL",
    )
    assert result.clean.height == 2
    assert result.quarantined.is_empty()


def test_no_arbitrage_tolerates_shallow_negative_forward() -> None:
    # forward = 2*0.024 - 0.05 = -0.002 (yellow, within -50bp).
    result = validate_curve_no_arbitrage(
        _zero_frame({1.0: 0.05, 2.0: 0.024}),
        regime="NOMINAL",
    )
    assert result.clean.height == 2
    assert result.quarantined.is_empty()


def test_no_arbitrage_quarantines_steep_nominal_inversion() -> None:
    # forward = 2*0.02 - 0.05 = -0.01 (red, beyond -50bp).
    result = validate_curve_no_arbitrage(
        _zero_frame({1.0: 0.05, 2.0: 0.02}),
        regime="NOMINAL",
    )
    assert result.clean.is_empty()
    assert result.quarantined.height == 2


def test_no_arbitrage_real_regime_tolerates_deeper_inversion() -> None:
    # forward = 2*(-0.005) - 0.05 = -0.06 (yellow for REAL: -80bp..-50bp).
    result = validate_curve_no_arbitrage(
        _zero_frame({1.0: 0.05, 2.0: -0.005}),
        regime="REAL",
    )
    assert result.clean.height == 2
    assert result.quarantined.is_empty()


def test_no_arbitrage_real_regime_quarantines_beyond_floor() -> None:
    # forward = 2*(-0.02) - 0.05 = -0.09 (red for REAL: below -80bp).
    result = validate_curve_no_arbitrage(
        _zero_frame({1.0: 0.05, 2.0: -0.02}),
        regime="REAL",
    )
    assert result.clean.is_empty()
    assert result.quarantined.height == 2


def test_no_arbitrage_rejects_unknown_regime() -> None:
    with pytest.raises(ValueError, match="regime"):
        validate_curve_no_arbitrage(_zero_frame({1.0: 0.05}), regime=cast(CurveRegime, "WEIRD"))


def test_negative_rates_relax_discount_factor_upper_bound() -> None:
    base = _cashflow_frame().with_columns(pl.lit(1.2).alias("discount_factor"))
    with pytest.raises(SchemaError):
        validate_ingestion(base, "fct_valued_cashflows")
    result = validate_ingestion(base, "fct_valued_cashflows", allow_negative_rates=True)
    assert result["discount_factor"].to_list() == [1.2]
    assert ValuedCashflowNegativeRatesSchema is not ValuedCashflowSchema
