---
name: data-resilience-standards
description: Enforces runtime schema-contract validation (Pandera + Polars) and guaranteed idempotency (natural-key upsert) for every BCCh/FRED/Alpaca ingestion path before data reaches DuckDB.
---

# Data Quality & Resilience Standards Skill

## Purpose
A financial data pipeline is not "working" merely because it runs. It must be
**idempotent**, **anomaly-tolerant**, and **strict about schemas**. This skill
makes every ingestion path (BCCh, FRED, Alpaca) fail fast on contract violations
**before** touching DuckDB, and guarantees that re-running a valuation for the
same `valuation_date` never duplicates rows.

Industry basis: Pandera (Polars/DuckDB ecosystems) and Great Expectations for
schema/data-quality expectations; ACID + natural-key upsert for idempotency.

## 1. Runtime Schema Contracts (Pandera)
Every `pl.DataFrame` must be validated against a Pandera schema
(`pandera.polars.DataFrameSchema`) BEFORE any `DELETE` / `INSERT` / `MERGE`
reaches DuckDB. Validation covers:

- **Strict dtypes:** `pl.Date`, `pl.Float64`, `pl.String`.
- **Prohibited nulls:** every non-optional column is non-nullable (Pandera default).
- **Logical ranges:** see the table below.

| Table | Column | Rule | Rationale |
| --- | --- | --- | --- |
| `fct_market_curves` | `tenor_yf` | `>= 0` | Year fraction cannot be negative |
| `fct_market_curves` | `zero_rate` | non-null, sign free | Negative policy rates are valid |
| `fct_fx_rates` | `spot_rate` | `> 0` | A price/rate of zero or below is invalid |
| `fct_valued_cashflows` | `coupon_rate` | `>= 0` by default | Negative only under extreme monetary policy, via explicit opt-in |
| `fct_valued_cashflows` | `yf` | `>= 0` | Year fraction cannot be negative |
| `fct_valued_cashflows` | `discount_factor` | strictly `0 < DF <= 1` | Time-value-of-money invariant |

Contracts live in `packages/risk-data-core/src/risk_data_core/validation.py` and
mirror the SQLAlchemy tables in `models.py`. The shared entry point is
`validate_ingestion(df, table_name)`; `RiskDatabase.bulk_insert_polars(...,
validate=True)` already calls it.

**Negative-coupon exception:** pass `allow_negative_coupon=True` only for a
documented extreme-monetary-policy regime. Never relax the rule silently.

## 2. Guaranteed Idempotency
Idempotency is a database property, not a Python promise:

1. Every `fct_*` table has a natural key enforced by a `PRIMARY KEY` / `UNIQUE`
   constraint.
2. Writes use either an atomic upsert
   (`INSERT ... ON CONFLICT (...) DO UPDATE SET ...`) or a full partition
   replacement (`replace_partition_col` / `replace_partition_val`).
3. Running the valuation pipeline **three times** for the same `valuation_date`
   MUST yield identical table contents with zero duplicate rows.

Canonical write (natural key = primary key):

```python
db.bulk_insert_polars(
    df_cf,
    "fct_valued_cashflows",
    conflict_columns=("valuation_date", "cashflow_id"),
)
```

`bulk_insert_polars` derives the conflict target from the table `PRIMARY KEY`
when `conflict_columns` is omitted, so upsert-on-natural-key is the default.
`replace_partition_col` and `conflict_columns` are mutually exclusive.

## 3. Ingestion Checklist (BCCh / FRED / Alpaca)
Before any external payload is persisted:

- [ ] Map the raw payload to a typed `pl.DataFrame` (never `pandas`).
- [ ] Run `validate_ingestion(df, table_name)`; abort on
      `pandera.errors.SchemaError`.
- [ ] Write with the natural-key upsert (or a partition replacement).
- [ ] Wrap the write in `timed(logger, "ingest_<source>", ...)` so `execution_time_ms` and `row_count` are recorded (see `observability-standards`).
- [ ] Re-run the same batch and assert row counts and values are unchanged.

## 4. Verification Protocol
Run before completing any ingestion or persistence task:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
