# Open Market Risk Engine (CL) — Agent Operating Laws

Monorepo en Python 3.12+ gestionado con `uv` workspaces para valorización de portafolios, cálculo de riesgo interno (VaR EWMA, Expected Shortfall, DV01) y capital regulatorio chileno (CMF RAN 21-7 / R07).

## 1. MANDATORY ROUTING & HARD-STOP PROTOCOL (RULE #1)
- **Before creating, editing, or deleting ANY code file**, you MUST load and follow the `meta-router-planner` skill.
- **Even if invoked in Build/Act mode**, if architectural scope, mathematical conventions, or edge cases are ambiguous and have not been clarified in the current conversation turn, you MUST output the routing plan + clarifying questions and **STOP immediately** without calling file modification tools (`write`, `edit`, `patch`).

## 2. ARCHITECTURE & WORKSPACE BOUNDARIES
- `packages/risk-data-core`: SQLAlchemy 2.0 ORM schemas, Pydantic contracts, and DuckDB connection manager. Shared kernel.
- `packages/portfolio-pricing-engine`: BCCh/FRED/Alpaca ingestion, bootstrapping, `trade -> leg -> cashflow` expansion, PV, and DV01. **Only package allowed to open DuckDB in `read_write` mode.**
- `packages/cmf-regulatory-r07`: CMF RAN 21-7 standard method, maturity/duration bands, vertical/horizontal disallowances, R07 & APRM. Opens DuckDB in `read_only=True`.
- `packages/market-risk-analytics`: Cashflow mapping, EWMA covariance (lambda=0.94), VaR 95%/99%, Expected Shortfall (CVaR), Kupiec POF backtesting. Opens DuckDB in `read_only=True`.

## 3. HARD TECHNICAL CONSTRAINTS
- **ZERO PANDAS POLICY:** `pandas` is strictly banned across the entire repository. Use `polars` (`pl.DataFrame`, `pl.LazyFrame`), `duckdb` SQL, and `numpy`/`scipy` arrays via `.to_numpy()`.
- **ORM vs. OLAP Separation:** Use SQLAlchemy ONLY for DDL, schema governance, and metadata CRUD. Never iterate ORM objects for pricing or risk math. Use Zero-Copy Apache Arrow between DuckDB and Polars (`duckdb-polars-hybrid` skill).
- **Mandatory TDD:** Every new function in `src/` requires deterministic unit/integration tests in `tests/` (`quant-tdd-enforcer` skill).

## 4. VERIFICATION GATE (MUST PASS BEFORE COMPLETION)
Never report a coding task as finished until all four commands return exit code `0`:
1. `uv run ruff check --fix .`
2. `uv run ruff format --check .`
3. `uv run ty check`
4. `uv run pytest -q`
