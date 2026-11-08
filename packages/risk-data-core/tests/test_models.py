from datetime import date

from risk_data_core.models import (
    BankingVolume,
    Base,
    FxRate,
    Leg,
    MarketCurve,
    MarketFactor,
    StgMarketCurve,
    StgMarketFactor,
    Trade,
    ValuedCashflow,
)
from sqlalchemy import inspect


def test_metadata_contains_required_tables_and_primary_keys() -> None:
    assert set(Base.metadata.tables) >= {
        "dim_trades",
        "dim_legs",
        "fct_market_curves",
        "fct_fx_rates",
        "fct_valued_cashflows",
        "fct_market_factors",
        "fct_banking_volumes",
        "stg_market_curves",
        "stg_market_factors",
    }
    assert [column.name for column in inspect(Trade).primary_key] == ["trade_id"]
    assert [column.name for column in inspect(Leg).primary_key] == ["leg_id"]
    assert {"notional", "payment_frequency"} <= set(Leg.__table__.columns.keys())
    assert [column.name for column in inspect(FxRate).primary_key] == [
        "valuation_date",
        "currency_pair",
    ]
    assert [column.name for column in inspect(MarketCurve).primary_key] == [
        "valuation_date",
        "curve_name",
        "tenor_label",
    ]
    assert [column.name for column in inspect(ValuedCashflow).primary_key] == [
        "valuation_date",
        "cashflow_id",
    ]
    assert [column.name for column in inspect(MarketFactor).primary_key] == [
        "valuation_date",
        "factor_code",
    ]
    assert [column.name for column in inspect(StgMarketCurve).primary_key] == [
        "valuation_date",
        "curve_name",
        "tenor_label",
    ]
    assert [column.name for column in inspect(StgMarketFactor).primary_key] == [
        "as_of_date",
        "factor_code",
    ]
    assert [column.name for column in inspect(BankingVolume).primary_key] == [
        "as_of_date",
        "instrument_code",
    ]


def test_valued_cashflow_contract_columns_include_risk_measures() -> None:
    required = {
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
    }
    assert required <= set(ValuedCashflow.__table__.columns.keys())
    assert date(2026, 1, 1).isoformat() == "2026-01-01"
