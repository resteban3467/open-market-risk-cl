from datetime import date

import numpy as np
import polars as pl
import pytest
from portfolio_pricing_engine.domain.bootstrapping import (
    ZeroCurve,
    bootstrap_swap_curve,
    bootstrap_zero_rates,
    tenor_to_years,
)


def test_tenor_to_years() -> None:
    assert tenor_to_years("1D") == pytest.approx(1 / 365)
    assert tenor_to_years("6M") == pytest.approx(0.5)
    assert tenor_to_years("10Y") == pytest.approx(10.0)
    with pytest.raises(ValueError, match="unsupported"):
        tenor_to_years("2W")


def test_flat_par_curve_bootstraps_to_log_one_plus_coupon() -> None:
    par = pl.DataFrame({"tenor_yf": [1.0, 2.0, 5.0, 10.0], "par_rate": [0.05] * 4})
    zeros = bootstrap_zero_rates(par)
    expected = float(np.log1p(0.05))
    assert zeros["zero_rate"].to_list() == pytest.approx([expected] * 4, rel=1e-12)


def test_two_node_bootstrap_matches_analytical() -> None:
    par = pl.DataFrame({"tenor_yf": [1.0, 2.0], "par_rate": [0.05, 0.06]})
    zeros = bootstrap_zero_rates(par)
    df1 = 1.0 / 1.05
    df2 = (1.0 - 0.06 * df1) / 1.06
    expected = [-np.log(df1) / 1.0, -np.log(df2) / 2.0]
    assert zeros["zero_rate"].to_list() == pytest.approx(expected, rel=1e-12)


def test_par_bootstrap_requires_integer_tenors() -> None:
    par = pl.DataFrame({"tenor_yf": [0.5], "par_rate": [0.05]})
    with pytest.raises(ValueError, match="integer-year"):
        bootstrap_zero_rates(par)


def test_zero_curve_discount_factors() -> None:
    par = pl.DataFrame({"tenor_yf": [1.0, 2.0], "par_rate": [0.05, 0.05]})
    curve = ZeroCurve.from_par_rates(par, date(2026, 1, 2))
    yf = np.array([0.0, 1.0, 2.0])
    assert curve.discount_factor(yf) == pytest.approx([1.0, 1 / 1.05, 1 / 1.05**2])


def _swap_nodes(labels: list[str], rates: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "tenor_label": labels,
            "tenor_yf": [tenor_to_years(label) for label in labels],
            "par_rate": rates,
        }
    )


def test_swap_short_end_simple_node_is_a_bullet() -> None:
    nodes = _swap_nodes(["90D"], [0.05])
    zeros = bootstrap_swap_curve(nodes, coupon_frequency="SEMIANNUAL", day_count="ACT/360")

    df = 1.0 / (1.0 + 0.05 * 0.25)
    expected = -float(np.log(df)) / tenor_to_years("90D")
    assert zeros["zero_rate"].to_list() == pytest.approx([expected], rel=1e-12)


def test_swap_flat_curve_bootstraps_to_continuous_equivalent() -> None:
    # Consistent construction: 6M @ 5% simple and 1Y @ 5.0625% simple reproduce
    # the flat 5% semiannual par curve; the zero rate must be 2*ln(1.025).
    nodes = _swap_nodes(
        ["6M", "1Y", "1.5Y", "2Y", "2.5Y", "3Y"],
        [0.05, 0.050625, 0.05, 0.05, 0.05, 0.05],
    )
    zeros = bootstrap_swap_curve(nodes, coupon_frequency="SEMIANNUAL")

    expected = 2.0 * float(np.log(1.025))
    assert zeros["zero_rate"].to_list() == pytest.approx([expected] * 6, rel=1e-9)


def test_swap_curve_reprices_quoted_par_swaps() -> None:
    nodes = _swap_nodes(
        ["6M", "1Y", "1.5Y", "2Y", "2.5Y", "3Y"],
        [0.05, 0.050625, 0.05, 0.05, 0.05, 0.05],
    )
    zeros = bootstrap_swap_curve(nodes, coupon_frequency="SEMIANNUAL")
    df = np.exp(-zeros["zero_rate"].to_numpy() * zeros["tenor_yf"].to_numpy())
    df_map = dict(zip(zeros["tenor_yf"].to_list(), df, strict=True))

    coupons = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    accrued = sum(0.5 * df_map[tenor] for tenor in coupons)
    assert 0.05 * accrued + df_map[3.0] == pytest.approx(1.0, rel=1e-9)


def test_swap_curve_discount_factors_are_decreasing() -> None:
    nodes = _swap_nodes(
        ["6M", "1Y", "1.5Y", "2Y", "2.5Y", "3Y"],
        [0.04, 0.045, 0.05, 0.05, 0.05, 0.05],
    )
    zeros = bootstrap_swap_curve(nodes, coupon_frequency="SEMIANNUAL")
    df = np.exp(-zeros["zero_rate"].to_numpy() * zeros["tenor_yf"].to_numpy())
    assert np.all(np.diff(df) < 0.0)


def test_swap_curve_rejects_unknown_coupon_frequency() -> None:
    nodes = _swap_nodes(["1Y", "2Y"], [0.05, 0.05])
    with pytest.raises(ValueError, match="coupon_frequency"):
        bootstrap_swap_curve(nodes, coupon_frequency="WEEKLY")


def test_zero_curve_from_zero_rates_round_trips() -> None:
    zeros = pl.DataFrame({"tenor_yf": [1.0, 2.0], "zero_rate": [0.05, 0.052]})
    curve = ZeroCurve.from_zero_rates(zeros, date(2026, 1, 2))
    assert curve.zero_rate(np.array([1.0, 2.0])) == pytest.approx([0.05, 0.052])
