---
name: clean-architecture-standards
description: Enforces Ports & Adapters (Hexagonal) boundaries and SOLID dependency inversion so HTTP clients stay decoupled from bootstrapping and analytical packages never import ingestion logic.
---

# Clean Architecture & Decoupling Standards Skill

## Purpose
Keep business logic (pricing, bootstrapping, risk math) independent from external
adapters (BCCh/FRED/Alpaca REST clients, DuckDB). Dependencies point inward:
adapters depend on the domain, never the reverse.

Industry basis: Ports & Adapters (Hexagonal Architecture, Alistair Cockburn) and
SOLID, especially the Dependency Inversion Principle.

## 1. Dependency Direction (one-way arrows)
```text
risk-data-core  (shared kernel: contracts, ORM, DuckDB)
        ^
        |  depends on
portfolio-pricing-engine  (ingestion adapters + pricing)
        ^
        |  reads only
market-risk-analytics / cmf-regulatory-r07  (analytical consumers)
```
Arrows point **inward / toward the kernel**. No package may import "upward".

## 2. Dependency Inversion in HTTP Clients
- Define a **port** (`typing.Protocol` or `abc.ABC`) for each external source.
- `bcch_client.py`, `fred_client.py`, and `alpaca_client.py` are **adapters** that
  implement that port and return *pure* structures:
  - `pl.DataFrame` validated by the `data-resilience-standards` Pandera schemas, or
  - typed Pydantic models.
- A raw `requests.Response`, session, retry state, or auth token MUST NOT cross
  the port. Bootstrapping consumes the port, never the concrete client.

```python
from datetime import date
from typing import Protocol

import polars as pl

from risk_data_core.validation import FxRateSchema


class FxRateProvider(Protocol):
    def fetch(self, valuation_date: date) -> pl.DataFrame: ...


class BcchFxClient:
    """Adapter: HTTP details stay inside this class."""

    def fetch(self, valuation_date: date) -> pl.DataFrame:
        payload = self._http_get(valuation_date)  # adapter detail
        return FxRateSchema.validate(self._to_frame(payload))
```

## 3. Unbreakable Boundaries
- `market-risk-analytics` and `cmf-regulatory-r07` MUST NOT import
  `portfolio-pricing-engine` (or any ingestion module).
- They read `fct_*` tables exclusively through `RiskDatabase` (`read_only=True`)
  from `risk-data-core`.
- `risk-data-core` MUST NOT import any downstream package.
- `portfolio-pricing-engine` may import only `risk-data-core`.

## 4. Enforcement
`packages/risk-data-core/tests/test_architecture.py` walks `packages/*/src` with
`ast` and fails on any illegal first-party import. It runs with the normal test
suite, so a boundary violation breaks CI immediately.

## 5. Verification Protocol
Run before completing any architectural or ingestion task:

```bash
uv run ruff check --fix .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```
