---
name: polars-vectorization
description: Enforces high-performance vectorized financial math in Polars and NumPy/SciPy without Python row loops or Pandas fallbacks.
---

# Polars Vectorization & Quantitative Math Skill

## Purpose
Ensure all curve interpolation, cashflow discounting, DV01 bump-and-revalue, Cashflow Mapping, and EWMA covariance calculations run natively in vectorized Polars expressions or contiguous NumPy/SciPy arrays.

## 1. Strictly Banned Constructs
- **NO PANDAS:** Never import `pandas` or call `.to_pandas()`.
- **NO PYTHON ROW LOOPS ON DATAFRAMES:**
  - Banned: `df.iter_rows()`, `df.rows()`, `[row for row in df.to_dicts()]` inside pricing or risk loops.
  - Banned: `pl.Expr.map_elements()` for arithmetic, discounting, or band classification. Use native `pl.when().then().otherwise()` and native math expressions (`pl.col("x").exp()`, `.log()`, `.pow()`).

## 2. Canonical Vectorized Financial Patterns

### Pattern A: Curve Interpolation & Discounting via `join_asof` or SciPy Splines
When interpolating curve rates onto arbitrary cashflow dates (`payment_date`):
1. Convert `payment_date` to year fraction `yf = (pl.col("payment_date") - pl.col("valuation_date")).dt.total_days() / 365.0`.
2. For Cubic Spline / Nelson-Siegel-Svensson across many curves, extract node arrays via `.to_numpy()`, evaluate vectorized `scipy.interpolate.CubicSpline(knot_yf, knot_rates)(target_yf_array)`, and attach back as a `pl.Series`.
3. Compute Discount Factors and Present Value entirely in Polars expressions:

```python
import polars as pl


def discount_cashflows_vectorized(df_cf: pl.DataFrame) -> pl.DataFrame:
    """Vectorized discounting and analytical/bump DV01 setup."""
    return df_cf.with_columns(
        discount_factor=(-pl.col("spot_rate") * pl.col("yf")).exp(),
        discount_factor_up=(-(pl.col("spot_rate") + 0.0001) * pl.col("yf")).exp(),
        discount_factor_dn=(-(pl.col("spot_rate") - 0.0001) * pl.col("yf")).exp(),
    ).with_columns(
        present_value_orig_ccy=pl.col("total_cashflow") * pl.col("discount_factor"),
        dv01_orig_ccy=-(
            pl.col("total_cashflow")
            * (pl.col("discount_factor_up") - pl.col("discount_factor_dn"))
            / 2.0
        ),
    )
```

### Pattern B: RiskMetrics Cashflow Mapping onto Standard Vertices
To map each cashflow at maturity $t \in [v_1, v_2]$ onto adjacent standard vertices $v_1$ and $v_2$ without loops:
- Use `df_cf.join_asof(...)` against the standard vertex table (`1M, 3M, 6M, 1Y, 2Y, 5Y, 10Y, 20Y`) both backward (`strategy="backward"` for $v_1$) and forward (`strategy="forward"` for $v_2$).
- Compute linear or variance-preserving weights $\alpha \in [0, 1]$ via native column expressions:
  `w_v2 = (pl.col("yf") - pl.col("v1_yf")) / (pl.col("v2_yf") - pl.col("v1_yf"))`
  `w_v1 = 1.0 - pl.col("w_v2")`

### Pattern C: EWMA Covariance Matrix ($\lambda = 0.94$) & VaR/ES via Polars -> NumPy Bridge
Pivot historical factor returns in Polars, extract a clean 2D `np.ndarray` (`T x K`), and compute exponentially weighted covariance in vectorized NumPy:

```python
import numpy as np
import numpy.typing as npt
import polars as pl


def compute_ewma_covariance(
    df_returns: pl.DataFrame,
    factor_cols: list[str],
    lam: float = 0.94,
) -> npt.NDArray[np.float64]:
    """Compute RiskMetrics EWMA covariance matrix from a sorted Polars return matrix."""
    ret_matrix = df_returns.select(factor_cols).to_numpy()  # Shape: (T, K)
    n_obs = ret_matrix.shape[0]
    # Exponential weights from oldest (t=0) to most recent (t=T-1)
    exponents = np.arange(n_obs - 1, -1, -1, dtype=np.float64)
    weights = (1.0 - lam) * (lam**exponents)
    weights /= weights.sum()  # Normalize for finite sample window

    weighted_returns = ret_matrix * weights[:, np.newaxis]
    cov_matrix: npt.NDArray[np.float64] = ret_matrix.T @ weighted_returns
    return cov_matrix
```

## 3. Verification Protocol
Before finishing any quantitative task, run:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
