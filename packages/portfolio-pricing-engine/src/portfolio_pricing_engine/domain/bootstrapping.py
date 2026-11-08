"""Yield-curve bootstrapping and interpolation.

Converts annual par-bond yields into continuously compounded zero rates with a
recursive par-to-zero bootstrap, then fits a cubic spline for arbitrary tenors.
Discount-factor evaluation is vectorized with NumPy/SciPy.

Two bootstrappers are provided:

- :func:`bootstrap_zero_rates` for annual-coupon par bonds (BCP, BCU, UST).
- :func:`bootstrap_swap_curve` for Chilean *Swap Promedio Cámara* (SPC) curves:
  a simple-rate (bullet) short end (<= 1Y) plus a par-swap long end (> 1Y) with
  a configurable coupon frequency.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy.interpolate import CubicSpline


def tenor_to_years(label: str) -> float:
    """Convert a tenor label such as ``1D``, ``6M`` or ``10Y`` to years."""
    text = label.strip().upper()
    if len(text) < 2:
        msg = f"invalid tenor label: {label!r}"
        raise ValueError(msg)
    unit = text[-1]
    amount = float(text[:-1])
    if unit == "D":
        return amount / 365.0
    if unit == "M":
        return amount / 12.0
    if unit == "Y":
        return amount
    msg = f"unsupported tenor label: {label!r}"
    raise ValueError(msg)


def _df_at(known: dict[int, float], year: int) -> float:
    """Return the discount factor at ``year``, flat-extrapolating if unknown."""
    if year in known:
        return known[year]
    lower = max(key for key in known if key < year)
    if lower == 0:
        return 1.0
    zero = -np.log(known[lower]) / lower
    return float(np.exp(-zero * year))


def bootstrap_zero_rates(par_rates: pl.DataFrame) -> pl.DataFrame:
    """Bootstrap zero rates from annual par-bond yields.

    ``par_rates`` must have ``tenor_yf`` and ``par_rate`` (decimal, e.g. ``0.05``).
    Assumes annual coupons and integer-year tenors. The returned frame has
    ``tenor_yf`` and ``zero_rate`` sorted by tenor.
    """
    nodes = par_rates.select(["tenor_yf", "par_rate"]).sort("tenor_yf")
    raw_tenors = nodes["tenor_yf"].to_list()
    tenors = [round(value) for value in raw_tenors]
    coupons = nodes["par_rate"].to_list()

    for raw, maturity in zip(raw_tenors, tenors, strict=True):
        if abs(raw - maturity) > 1e-9:
            msg = f"par bootstrap requires integer-year tenors, got {raw}"
            raise ValueError(msg)

    known: dict[int, float] = {0: 1.0}
    for maturity, coupon in zip(tenors, coupons, strict=True):
        accrued = sum(_df_at(known, year) for year in range(1, maturity))
        known[maturity] = (1.0 - coupon * accrued) / (1.0 + coupon)

    return pl.DataFrame(
        {
            "tenor_yf": [float(tenor) for tenor in tenors],
            "zero_rate": [float(-np.log(known[tenor]) / tenor) for tenor in tenors],
        }
    )


_COUPON_STEP: dict[str, float] = {"SEMIANNUAL": 0.5, "ANNUAL": 1.0}


def _simple_alpha(tenor_label: str, day_count: str) -> float:
    """Return the year fraction for a simple-rate node under ``day_count``."""
    text = tenor_label.strip().upper()
    if len(text) < 2:
        msg = f"invalid tenor label: {tenor_label!r}"
        raise ValueError(msg)
    unit = text[-1]
    amount = float(text[:-1])
    if unit == "D":
        return amount / (360.0 if day_count in ("ACT/360", "30/360") else 365.0)
    if unit == "M":
        if day_count == "ACT/365":
            return amount / 12.0
        return amount * 30.0 / 360.0
    if unit == "Y":
        return amount
    msg = f"unsupported tenor label: {tenor_label!r}"
    raise ValueError(msg)


def _interp(xs: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear interpolation over sorted ``(x, y)`` points."""
    if x <= xs[0][0]:
        return xs[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(xs):
        if x <= x1:
            if abs(x1 - x0) < 1e-12:
                return y1
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return xs[-1][1]


def bootstrap_swap_curve(
    nodes: pl.DataFrame,
    *,
    coupon_frequency: str = "SEMIANNUAL",
    day_count: str = "ACT/360",
) -> pl.DataFrame:
    """Bootstrap a zero curve from a swap curve (simple short end + par long end).

    ``nodes`` must have ``tenor_label``, ``tenor_yf`` (ACT/365 year fraction) and
    ``par_rate`` (decimal). The short end (``tenor_yf <= 1``) is a bullet paying
    simple interest under ``day_count``; the long end (> 1Y) is a par swap paying
    ``coupon_frequency`` coupons on a regular grid.

    The returned frame has one row per input node (``tenor_yf``, ``zero_rate``)
    sorted by ``tenor_yf``, so callers can re-attach tenor labels positionally.
    Zero rates are continuously compounded: ``z = -ln(DF) / tenor_yf``.
    """
    if coupon_frequency not in _COUPON_STEP:
        msg = f"unsupported coupon_frequency: {coupon_frequency!r}"
        raise ValueError(msg)
    step = _COUPON_STEP[coupon_frequency]

    ordered = nodes.select(["tenor_label", "tenor_yf", "par_rate"]).sort("tenor_yf")
    labels = ordered["tenor_label"].to_list()
    tenors = [float(value) for value in ordered["tenor_yf"].to_list()]
    rates = [float(value) for value in ordered["par_rate"].to_list()]

    # 1. Short-end discount factors at quoted nodes (t <= 1Y), from simple rates.
    short_points: list[tuple[float, float]] = [(0.0, 1.0)]
    df_by_t: dict[float, float] = {0.0: 1.0}
    for label, t, s in zip(labels, tenors, rates, strict=True):
        if t <= 1.0 + 1e-9:
            alpha = _simple_alpha(label, day_count)
            df = 1.0 / (1.0 + s * alpha)
            df_by_t[t] = df
            short_points.append((t, df))
    short_points.sort()

    # 2. Coupon grid and par-rate interpolator over the long nodes.
    max_t = max(tenors)
    n_steps = math.floor(max_t / step + 1e-9)
    grid = [round(k * step, 10) for k in range(1, n_steps + 1)]
    par_points = sorted((t, s) for t, s in zip(tenors, rates, strict=True) if t >= 1.0 - 1e-9)

    # 3. Fill short-side grid points from the simple curve.
    for t in grid:
        if t <= 1.0 + 1e-9:
            df_by_t.setdefault(t, _interp(short_points, t))

    # 4. Sequential par bootstrap over grid points > 1Y.
    for t in grid:
        if t <= 1.0 + 1e-9 or t in df_by_t:
            continue
        coupon = _interp(par_points, t)
        accrued = sum(step * df_by_t[u] for u in grid if u < t - 1e-12 and u in df_by_t)
        df_by_t[t] = (1.0 - coupon * accrued) / (1.0 + coupon * step)

    # 5. Zero rates at the original quoted tenors.
    grid_points = sorted(df_by_t.items())
    zeros: list[float] = []
    for t in tenors:
        df = df_by_t.get(t)
        if df is None:
            df = _interp(grid_points, t)
        if df <= 0.0:
            msg = f"non-positive discount factor at tenor_yf={t}"
            raise ValueError(msg)
        zeros.append(float(-math.log(df) / t) if t > 0.0 else 0.0)

    return pl.DataFrame({"tenor_yf": tenors, "zero_rate": zeros}).sort("tenor_yf")


@dataclass(frozen=True)
class ZeroCurve:
    """Continuously compounded zero curve backed by a cubic spline."""

    valuation_date: date
    spline: CubicSpline

    @classmethod
    def from_par_rates(cls, par_rates: pl.DataFrame, valuation_date: date) -> ZeroCurve:
        """Bootstrap par rates and fit a cubic spline over the zero nodes."""
        zeros = bootstrap_zero_rates(par_rates)
        spline = CubicSpline(
            zeros["tenor_yf"].to_numpy(),
            zeros["zero_rate"].to_numpy(),
            extrapolate=True,
        )
        return cls(valuation_date=valuation_date, spline=spline)

    @classmethod
    def from_zero_rates(cls, zero_rates: pl.DataFrame, valuation_date: date) -> ZeroCurve:
        """Fit the spline directly over already-bootstrapped zero nodes."""
        nodes = zero_rates.select(["tenor_yf", "zero_rate"]).sort("tenor_yf")
        spline = CubicSpline(
            nodes["tenor_yf"].to_numpy(),
            nodes["zero_rate"].to_numpy(),
            extrapolate=True,
        )
        return cls(valuation_date=valuation_date, spline=spline)

    def zero_rate(self, yf: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        """Evaluate continuously compounded zero rates at ``yf``."""
        return np.asarray(self.spline(yf), dtype=np.float64)

    def discount_factor(
        self,
        yf: npt.NDArray[np.float64],
        bump: float = 0.0,
    ) -> npt.NDArray[np.float64]:
        """Evaluate discount factors, optionally bumped by ``bump`` (decimal)."""
        rates = self.zero_rate(yf) + bump
        return np.asarray(np.exp(-rates * yf), dtype=np.float64)


__all__ = ["ZeroCurve", "bootstrap_swap_curve", "bootstrap_zero_rates", "tenor_to_years"]
