---
name: meta-router-planner
description: Mandatory first-step orchestrator for any feature, refactor, or bugfix. Analyzes scope, selects required domain/technical skills, and enforces clarifying questions before any file edits.
---

# Meta-Router & Scope Planner Skill

## Purpose
Prevent premature code generation, silent financial assumptions, and cross-package boundary violations by routing tasks to specialized skills and verifying requirements with the user first.

## 1. Execution Brake (Hard-Stop Rule)
When triggered by a user request that involves writing or modifying code:
1. **DO NOT** invoke `write`, `edit`, `patch`, or file-mutating bash commands if there is any ambiguity in financial conventions, schema design, or scope.
2. Even if the agent is currently running in **Build/Act mode**, you MUST pause, present the structured Execution Plan + Clarifying Questions below, and wait for the user's explicit confirmation.
3. Only proceed directly to code execution once the user has answered your scoping questions (or explicitly states "procede con tus supuestos recomendados").

## 2. Skill Routing Matrix
Evaluate the task and queue the exact combination of skills required:
- **Database schemas, SQLAlchemy models, or DuckDB queries/inserts:** Queue `duckdb-polars-hybrid`.
- **Data ingestion (BCCh/FRED/Alpaca), schema contracts, or idempotency:** Queue `data-resilience-standards`.
- **Architecture, ports/adapters, HTTP clients, or package boundaries:** Queue `clean-architecture-standards`.
- **Structured logging, timing, profiling, or resource release:** Queue `observability-standards`.
- **Yield curves, bootstrapping, cashflow projection, DV01, EWMA matrices, VaR/CVaR:** Queue `polars-vectorization`.
- **CMF regulatory rules, R07 report, maturity/duration bands, APRM:** Queue `cmf-ran-21-7-rules`.
- **ANY creation or modification of logic in `src/`:** Queue `quant-tdd-enforcer` (MANDATORY).
- **Git commit or repository staging requests:** Queue `conventional-git-committer`.

## 3. Mandatory Ambiguity Check (Ask 1 to 3 High-Signal Questions)
Before coding, check if the prompt leaves any of the following undefined. If so, ask concise, technical questions offering a recommended default:
- **Day Count & Compounding Conventions:** e.g., `ACT/360` vs `ACT/365` vs `30/360`; continuous (`exp(-r*t)`), simple, or compound discounting `(1+r)**(-t)`.
- **Curve & Interpolation Rules:** e.g., Cubic spline vs log-linear on discount factors; behavior on extrapolation at short/long ends.
- **Data Anomalies & Missing Values:** e.g., Holidays in BCCh/FRED series, missing tenors, negative rates.
- **Package Ownership:** Which package in `packages/*` owns the interface vs the implementation.

## 4. Required Output Template
Always output your planning response in this exact format before waiting for user confirmation:

```text
### Plan de Enrutamiento y Alcance
- **Paquetes afectados:** [listar paquetes de `packages/*`]
- **Skills activadas:** [listar skills que se cargarán para ejecutar]
- **Archivos a crear/modificar:**
  - `packages/.../src/...`
  - `packages/.../tests/test_...`

### Preguntas de Definición (Confirmar antes de codificar)
1. **[Tema Técnico 1]:** [Pregunta concreta] -> *Recomendación por defecto: [X]*
2. **[Tema Técnico 2]:** [Pregunta concreta] -> *Recomendación por defecto: [Y]*
```
