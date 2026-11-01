---
name: observability-standards
description: Enforces structured logging with mandatory context (valuation_date, trade_count, execution_time_ms) via structlog and DuckDB/Polars profiling and resource-release practices.
---

# Observability & Telemetry Standards Skill

## Purpose
When the engine processes thousands of trades and complex curves, you must know
where time is spent and what failed. This skill standardizes **structured
logging with context** and **DuckDB/Polars profiling and resource hygiene**.

Industry basis: structlog for key-value structured logging; Polars/DuckDB
performance guidance (single `collect()`, explicit cursor/connection release,
optimized queries instead of nested subqueries).

## 1. Structured Logging with Context
Every critical process (BCCh/FRED/Alpaca ingestion, bootstrapping, cashflow
expansion, R07 grouping, VaR/ES) MUST emit structured traces, never `print()` or
plain `logging`.

Mandatory metadata keys:

| Key | Type | When |
| --- | --- | --- |
| `valuation_date` | ISO date / `date` | Any valuation-scoped process |
| `trade_count` | `int` | Portfolio-level pricing/risk runs |
| `execution_time_ms` | `float` | Every timed operation |

Canonical pattern:

```python
from risk_data_core.observability import get_logger, timed

logger = get_logger(__name__)

with timed(logger, "price_portfolio", valuation_date="2026-01-02", trade_count=128):
    df_valued = price_portfolio(...)
```

`timed()` emits `execution_time_ms` on exit, even if the block raises, so failed
runs still leave a trace.

## 2. DuckDB / Polars Profiling & Resource Hygiene
- **Close explicitly:** use `with engine.connect()` / `with engine.begin()` and
  call `RiskDatabase.close()` (which disposes the engine). Never leave a cursor
  or registered view dangling; always `unregister` temporary views in a `finally`.
- **Polars eager at boundaries:** call `.collect()` exactly once before leaving a
  `LazyFrame` scope. Never return an un-collected `LazyFrame` from a pipeline
  stage or a public function.
- **DuckDB query shape:** prefer one set-based `INSERT ... SELECT` over nested
  subqueries and per-row Python loops. Inspect plans with `EXPLAIN` (and
  `PRAGMA enable_profiling`) before optimizing.
- **No silent work:** log `row_count` and `table_name` on writes so throughput
  anomalies are visible.

## 3. Instrumentation Checklist
- [ ] `get_logger(__name__)` at module scope (no global mutable logger).
- [ ] Wrap each critical block in `timed(...)` with relevant context.
- [ ] Log failures with the same context keys before re-raising.
- [ ] Release every cursor, registered view, and connection explicitly.

## 4. Verification Protocol
Run before completing any observability or performance task:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
