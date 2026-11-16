from datetime import date

import numpy as np
import polars as pl
import pytest
from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve
from portfolio_pricing_engine.domain.cashflow_engine import compute_pv_dv01, expand_cashflows

VALUATION_DATE = date(2025, 1, 2)


def _flat_curve() -> ZeroCurve:
    par = pl.DataFrame({"tenor_yf": [1.0, 2.0, 5.0, 10.0], "par_rate": [0.05] * 4})
    return ZeroCurve.from_par_rates(par, VALUATION_DATE)


def _trades(instrument_type: str, maturity: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": ["t1"],
            "instrument_type": [instrument_type],
            "book": ["BANKING"],
            "trade_date": [VALUATION_DATE],
            "maturity_date": [maturity],
            "counterparty": [None],
        }
    )


def _legs(notional: float, coupon_rate: float, frequency: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "leg_id": ["l1"],
            "trade_id": ["t1"],
            "leg_type": ["ACTIVE"],
            "currency": ["CLP"],
            "notional": [notional],
            "rate_type": ["FIXED"],
            "day_count_convention": ["ACT/365"],
            "compounding_convention": ["CONTINUOUS"],
            "coupon_rate": [coupon_rate],
            "payment_frequency": [frequency],
            "reference_index": [None],
        }
    )


def test_par_bond_prices_at_par() -> None:
    trades = _trades("BOND", date(2027, 1, 2))
    legs = _legs(100.0, 0.05, "ANNUAL")
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)

    assert cashflows["total_cashflow"].to_list() == pytest.approx([5.0, 105.0])
    assert cashflows["yf"].to_list() == pytest.approx([1.0, 2.0])

    valued = compute_pv_dv01(cashflows, _flat_curve())
    assert valued["present_value_orig_ccy"].sum() == pytest.approx(100.0)


def test_zero_coupon_forward_pv_and_dv01() -> None:
    trades = _trades("FORWARD", date(2026, 1, 2))
    legs = _legs(1_000_000.0, 0.0, "ANNUAL")
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)
    assert cashflows.height == 1

    valued = compute_pv_dv01(cashflows, _flat_curve())
    expected_df = 1.0 / 1.05
    assert valued["discount_factor"][0] == pytest.approx(expected_df)
    assert valued["present_value_orig_ccy"][0] == pytest.approx(1_000_000.0 * expected_df)

    rate = float(np.log1p(0.05))
    df_up = float(np.exp(-(rate + 0.0001) * 1.0))
    df_dn = float(np.exp(-(rate - 0.0001) * 1.0))
    expected_dv01 = -(1_000_000.0 * (df_up - df_dn) / 2.0)
    assert valued["dv01_orig_ccy"][0] == pytest.approx(expected_dv01)


def test_semiannual_bond_accrues_act365_coupons() -> None:
    trades = _trades("BOND", date(2026, 1, 2))
    legs = _legs(100.0, 0.06, "SEMIANNUAL")
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)

    assert cashflows.height == 2
    first = 100.0 * 0.06 * (date(2025, 7, 2) - date(2025, 1, 2)).days / 365.0
    final = 100.0 * 0.06 * (date(2026, 1, 2) - date(2025, 7, 2)).days / 365.0 + 100.0
    assert cashflows["total_cashflow"].to_list() == pytest.approx([first, final])
    assert cashflows["total_cashflow"].sum() == pytest.approx(106.0)


def test_forwards_and_spots_are_single_bullets() -> None:
    for instrument in ("FORWARD", "SPOT"):
        trades = _trades(instrument, date(2026, 1, 2))
        legs = _legs(500.0, 0.0, "ANNUAL")
        cashflows = expand_cashflows(trades, legs, VALUATION_DATE)

        assert cashflows.height == 1
        assert cashflows["total_cashflow"].to_list() == pytest.approx([500.0])


def test_act_360_year_fraction_differs_from_act_365() -> None:
    trades = _trades("FORWARD", date(2026, 1, 2))
    legs = _legs(100.0, 0.0, "ANNUAL").with_columns(pl.lit("ACT/360").alias("day_count_convention"))
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)
    assert cashflows["yf"].to_list() == pytest.approx([365.0 / 360.0])


def test_past_coupons_are_excluded_from_valuation() -> None:
    trades = _trades("BOND", date(2028, 1, 2))
    legs = _legs(100.0, 0.05, "ANNUAL")

    cashflows = expand_cashflows(trades, legs, date(2026, 10, 30))

    assert cashflows["payment_date"].to_list() == [date(2027, 1, 2), date(2028, 1, 2)]


def test_uf_leg_converts_pv_and_dv01_with_factor() -> None:
    trades = _trades("FORWARD", date(2026, 1, 2))
    legs = _legs(1_000_000.0, 0.0, "ANNUAL").with_columns(pl.lit("UF").alias("currency"))
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)
    assert cashflows["currency"].to_list() == ["UF"]

    valued = compute_pv_dv01(
        cashflows,
        _flat_curve(),
        conversion_factors={"CLP": 1.0, "UF": 40_000.0},
    )
    expected_df = 1.0 / 1.05
    expected_pv_uf = 1_000_000.0 * expected_df
    assert valued["present_value_orig_ccy"][0] == pytest.approx(expected_pv_uf)
    assert valued["present_value_clp"][0] == pytest.approx(expected_pv_uf * 40_000.0)
    assert valued["dv01_clp"][0] == pytest.approx(valued["dv01_orig_ccy"][0] * 40_000.0)


def test_missing_conversion_factor_raises() -> None:
    trades = _trades("FORWARD", date(2026, 1, 2))
    legs = _legs(100.0, 0.0, "ANNUAL").with_columns(pl.lit("USD").alias("currency"))
    cashflows = expand_cashflows(trades, legs, VALUATION_DATE)

    with pytest.raises(ValueError, match="conversion factor"):
        compute_pv_dv01(cashflows, _flat_curve(), conversion_factors={"CLP": 1.0})


def test_bullet_loan_act360_flow_by_flow_repricing() -> None:
    valuation = date(2025, 1, 1)
    maturity = date(2028, 1, 1)
    notional = 100_000_000.0
    payment_dates = [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)]

    # ACT/360 discounting axis: cumulative days / 360.
    yf = np.array([(payment - valuation).days / 360.0 for payment in payment_dates])
    zero = 0.05
    curve = ZeroCurve.from_zero_rates(
        pl.DataFrame({"tenor_yf": yf.tolist(), "zero_rate": [zero] * len(yf)}),
        valuation,
    )
    df = np.exp(-zero * yf)

    accrual_starts = [valuation, *payment_dates[:-1]]
    alphas = np.array(
        [
            (payment - start).days / 360.0
            for start, payment in zip(accrual_starts, payment_dates, strict=True)
        ]
    )
    k_par = (1.0 - df[-1]) / float(np.sum(alphas * df))

    flows = np.array([notional * k_par * alpha for alpha in alphas])
    flows[-1] += notional
    manual_pv = float(np.sum(flows * df))
    assert manual_pv == pytest.approx(notional, rel=1e-12)

    trades = pl.DataFrame(
        {
            "trade_id": ["loan"],
            "instrument_type": ["BOND"],
            "book": ["BANKING"],
            "trade_date": [valuation],
            "maturity_date": [maturity],
            "counterparty": [None],
        }
    )
    legs = pl.DataFrame(
        {
            "leg_id": ["loan-leg"],
            "trade_id": ["loan"],
            "leg_type": ["ACTIVE"],
            "currency": ["CLP"],
            "notional": [notional],
            "rate_type": ["FIXED"],
            "day_count_convention": ["ACT/360"],
            "compounding_convention": ["SIMPLE"],
            "coupon_rate": [k_par],
            "payment_frequency": ["ANNUAL"],
            "reference_index": [None],
        }
    )

    cashflows = expand_cashflows(trades, legs, valuation)
    valued = compute_pv_dv01(cashflows, curve)

    assert cashflows["total_cashflow"].to_numpy() == pytest.approx(flows, rel=1e-12)
    assert float(valued["present_value_orig_ccy"].sum()) == pytest.approx(manual_pv, rel=1e-12)
    assert float(valued["present_value_orig_ccy"].sum()) == pytest.approx(notional, rel=1e-12)
