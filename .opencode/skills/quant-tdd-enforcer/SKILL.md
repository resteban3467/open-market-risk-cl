---
name: quant-tdd-enforcer
description: Enforces strict quantitative unit and integration testing standards using pytest, Polars testing utilities, in-memory DuckDB fixtures, and closed-form analytical invariants.
---

# Quantitative TDD & Testing Standards Skill

## Purpose
Ensure every module in `packages/*/src/` is backed by mathematically rigorous, deterministic tests in `packages/*/tests/`. Trivial assertions and mocked financial calculations are strictly forbidden.

## 1. Zero-Mock Policy for Financial Logic
- **BANNED:** Never use `unittest.mock.patch`, `MagicMock`, or `monkeypatch` to fake the output of curve bootstrapping, discount factors, DV01, EWMA covariance, VaR/CVaR, or RAN 21-7 band allocations.
- **ALLOWED:** Mocks are ONLY permitted at the external HTTP boundary (BCCh REST API, FRED API, Alpaca API) using realistic static JSON payloads stored in `tests/fixtures/`.
- **Database Tests:** All database integration tests MUST run against an ephemeral in-memory DuckDB instance (`RiskDatabase(Path(":memory:"), read_only=False)`).

## 2. The 3 Mandatory Test Tiers for New Code
Whenever you implement a quantitative or data module, your test file MUST include:

### Tier A: Closed-Form Analytical "Toy Model" Test
Verify the implementation against a known hand-calculable result using `pytest.approx(rel=1e-6)`:
- *Discounting & PV:* A flat continuous zero rate `r = 0.05` at `t = 1.0` year for a `1,000,000 CLP` cashflow MUST yield `DF = exp(-0.05) = 0.95122942` and `PV = 951,229.4245`.
- *DV01:* On a single zero-coupon cashflow at `t`, analytical DV01 must match numerical Bump & Revalue (`+/- 1 bp`) within `rel=1e-4`.
- *Parametric VaR & ES:* For a single asset with `mu = 0`, `sigma = 0.02`, position `= 1,000,000`, verify `VaR_95 = 1,000,000 * 1.6448536 * 0.02`.

### Tier B: Quantitative Invariant / Property Test
Verify mathematical laws that must hold for any valid portfolio:
- **Discount Factors:** `DF(0) = 1.0`, and for positive spot rates `r(t) > 0`, `0 < DF(t2) < DF(t1) <= 1.0` whenever `t2 > t1`.
- **Coherent Risk Ordering:** For the exact same portfolio and confidence level `alpha`, `Expected Shortfall (CVaR) >= VaR >= 0`.
- **Cashflow Mapping Conservation:** When mapping a cashflow `PV_i` across two adjacent standard maturity vertices `(v1, v2)`, the sum of mapped present values must equal `PV_i` (`PV_v1 + PV_v2 == PV_i`).
- **EWMA Covariance Symmetry & PSD:** The covariance matrix `Sigma_t` must be symmetric (`Sigma_t == Sigma_t.T`) and all eigenvalues must be `>= -1e-12` (Positive Semi-Definite).

### Tier C: Edge-Case & Polars Schema Test
- Assert exact output schemas and values using `polars.testing.assert_frame_equal(result_df, expected_df, check_dtypes=True, rel_tol=1e-6)`.
- Test boundary conditions: empty `pl.DataFrame` with valid schema, cashflow occurring on `valuation_date` (`t = 0`), and zero-rate curves (`r = 0 -> DF = 1.0`).

### Tier D: Data-Resilience Tests (Contracts & Idempotency)
- **Contract tests:** assert that every ingestion path rejects invalid data
  *before* writing. A `spot_rate <= 0`, a `discount_factor` outside `(0, 1]`, a
  prohibited null, or an unexpected column MUST raise
  `pandera.errors.SchemaError` and leave the target table empty.
- **Negative-coupon exception:** verify `coupon_rate < 0` is rejected by default
  and accepted only when `allow_negative_coupon=True` is explicit.
- **Triple-run idempotency:** insert the same valuation batch three times for one
  `valuation_date` against an in-memory DuckDB, then assert:
  `assert result.height == expected_height` and `result.to_dicts() == expected.to_dicts()`.
- **Upsert by natural key:** re-insert a corrected row with the same primary key
  and assert the stored value is updated in place, with no duplicate row.

## 3. Code Pattern Example

```python
import math
import polars as pl
import pytest

def test_zero_coupon_pv_analytical_and_invariants() -> None:
    rate = 0.05
    t = 2.0
    notional = 10_000.0
    expected_df = math.exp(-rate * t)
    expected_pv = notional * expected_df

    df_input = pl.DataFrame({
        "cashflow_id": ["cf_1"],
        "yf": [t],
        "spot_rate": [rate],
        "nominal_amount": [notional],
    })

    df_res = compute_present_value(df_input)

    assert df_res["discount_factor"][0] == pytest.approx(expected_df, rel=1e-7)
    assert df_res["present_value"][0] == pytest.approx(expected_pv, rel=1e-7)
    assert 0.0 < df_res["discount_factor"][0] <= 1.0
```

## 4. Verification Protocol
Run before completing any task:

```bash
uv run ruff check --fix .
uv run ty check
uv run pytest -v
```
