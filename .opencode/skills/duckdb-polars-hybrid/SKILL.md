---
name: duckdb-polars-hybrid
description: Enforces the hybrid SQLAlchemy 2.0 ORM + Zero-Copy Apache Arrow (DuckDB <-> Polars) architecture, read/write lock boundaries, and column naming standards across the monorepo.
---

# DuckDB + Polars + SQLAlchemy Hybrid Architecture Skill

## Purpose
Prevent ORM performance bottlenecks, DuckDB file-locking crashes, and forbidden `pandas` conversions when moving data between `risk-data-core` and downstream packages.

## 1. Strict Separation of Responsibilities (ORM vs. OLAP)
- **SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped`, `mapped_column`) is ONLY for:**
  1. Defining DDL schemas and table creation (`Base.metadata.create_all(engine)`).
  2. Inserting or querying small contract header tables (`dim_trades`, `dim_legs`, `dim_counterparties`).
- **DuckDB Native + Polars (`Zero-Copy Arrow`) is MANDATORY for:**
  1. Bulk reading/writing market time-series (`fct_market_curves`, `fct_fx_rates`, `fct_equity_prices`).
  2. Bulk reading/writing expanded and valued cashflows (`fct_valued_cashflows`).
  3. Any analytical aggregation, bootstrapping, cashflow mapping, or R07 regulatory grouping.
- **BANNED ANTIPATTERNS:**
  - Never iterate over `session.scalars(select(ValuedCashflow)).all()` to compute PV, DV01, VaR, or R07 bands.
  - Never use `.df()` on a DuckDB relation (it returns a Pandas DataFrame). Always use `.pl()` or `.arrow()`.
  - Never import ingestion code (`portfolio-pricing-engine`) from `market-risk-analytics` or `cmf-regulatory-r07`; they read `fct_*` only through `RiskDatabase` (see `clean-architecture-standards`).

## 2. Concurrency & File-Locking Law
DuckDB allows only ONE `read_write` process or MULTIPLE `read_only=True` processes:
- `packages/portfolio-pricing-engine`: Opens `RiskDatabase(db_path, read_only=False)`.
- `packages/cmf-regulatory-r07`: MUST open `RiskDatabase(db_path, read_only=True)`.
- `packages/market-risk-analytics`: MUST open `RiskDatabase(db_path, read_only=True)`.
- `tests/`: Always use `RiskDatabase(Path(":memory:"), read_only=False)`.
- **Resource hygiene:** always close the database (`RiskDatabase.close()`), rely on `with engine.connect()` / `with engine.begin()` context managers, and `unregister` temporary views in a `finally` (see `observability-standards`).

## 3. Canonical Connection & Zero-Copy Pattern
Always use or extend this exact pattern inside `risk-data-core`:

```python
from pathlib import Path
import duckdb
import polars as pl
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class RiskDatabase:
    def __init__(self, db_path: Path, read_only: bool = True) -> None:
        self.db_path = db_path
        self.read_only = read_only
        is_memory = str(db_path) == ":memory:"
        connect_args = {} if is_memory else {"read_only": self.read_only}
        self.engine = create_engine(f"duckdb:///{self.db_path}", connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init_schema(self) -> None:
        if self.read_only:
            msg = "Cannot initialize schema in read_only mode."
            raise PermissionError(msg)
        Base.metadata.create_all(self.engine)

    def query_polars(self, sql: str, params: dict[str, object] | None = None) -> pl.DataFrame:
        """Zero-copy query returning a Polars DataFrame via Apache Arrow."""
        with duckdb.connect(str(self.db_path), read_only=self.read_only) as con:
            rel = con.execute(sql, params) if params else con.execute(sql)
            return rel.pl()

    def bulk_insert_polars(self, df: pl.DataFrame, table_name: str) -> None:
        """Zero-copy bulk insert from Polars DataFrame into an existing DuckDB table."""
        if self.read_only:
            msg = f"Cannot insert into {table_name} in read_only mode."
            raise PermissionError(msg)
        if df.is_empty():
            return
        with duckdb.connect(str(self.db_path), read_only=False) as con:
            con.register("tmp_polars_view", df)
            cols = ", ".join(df.columns)
            con.execute(f"INSERT INTO {table_name} ({cols}) SELECT {cols} FROM tmp_polars_view")
            con.unregister("tmp_polars_view")
```

## 4. Pre-Ingestion Contract Gate & Idempotent Upsert
No DataFrame reaches DuckDB without a runtime contract check. Every bulk write
MUST go through the `data-resilience-standards` skill:

1. **Validate first:** `bulk_insert_polars(..., validate=True)` (the default)
   runs `validate_ingestion(df, table_name)` from
   `risk_data_core.validation` **before** any `DELETE` / `INSERT`. Contract
   violations raise `pandera.errors.SchemaError` while the database is untouched.
2. **Upsert on the natural key:** every `fct_*` write uses
   `INSERT ... ON CONFLICT (<primary key>) DO UPDATE SET ...`. When
   `conflict_columns` is omitted, it is derived from the table `PRIMARY KEY`.
3. **Partition replacement alternative:** `replace_partition_col` /
   `replace_partition_val` delete the target partition and re-insert it in one
   transaction. It is mutually exclusive with `conflict_columns`.
4. **Triple-run invariant:** executing the same valuation three times for one
   `valuation_date` MUST produce identical contents with no duplicate rows.

```python
db.bulk_insert_polars(df_cf, "fct_valued_cashflows")  # upsert on PK
db.bulk_insert_polars(
    df_cf,
    "fct_valued_cashflows",
    conflict_columns=("valuation_date", "cashflow_id"),
)
```

## 5. Data Standards & Column Units
- **Rates & Yields:** Always stored as decimal floats (`0.0525` for `5.25%`), NEVER as percentages (`5.25`).
- **Year Fractions (`yf`):** Float (`pl.Float64`) computed explicitly according to the leg's day-count convention (`ACT/360`, `ACT/365`, `30/360`).
- **Monetary Amounts:** Store both original currency (`present_value_orig_ccy`, `dv01_orig_ccy`) and CLP-converted (`present_value_clp`, `dv01_clp`) in `pl.Float64`.
- **Dates:** Always native `pl.Date` (`datetime.date`), never string timestamps.

## 6. Verification Protocol
Before finishing any database or schema task, run:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
