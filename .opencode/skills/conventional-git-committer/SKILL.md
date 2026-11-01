---
name: conventional-git-committer
description: Audits staged/unstaged changes, runs deterministic quality gates (ruff, ty, pytest), checks for leaked secrets or heavy data files, and creates atomic Conventional Commits scoped by monorepo package.
---

# Conventional Git Committer Skill

## Purpose
Maintain a clean, institutional-grade Git history suitable for technical portfolio review while preventing broken code, secrets, or binary database dumps from entering version control.

## 1. Mandatory Pre-Commit Quality Gate
Before running `git commit`, you MUST execute all four verification commands:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```

- **ABORT RULE:** If ANY command returns a non-zero exit code, DO NOT commit. Fix the underlying issue or report the failure to the user.

## 2. Security & Hygiene Audit
Inspect `git status --short` and `git diff`:
1. **Forbidden Files:** Never stage `.env`, API tokens (BCCh, FRED, Alpaca), `*.duckdb`, `*.duckdb.wal`, `*.parquet`, or `__pycache__/`. If any are untracked and not in `.gitignore`, add them to `.gitignore` first.
2. **Atomic Grouping:** If changes span multiple independent packages (e.g., a schema migration in `risk-data-core` and a new VaR test in `market-risk-analytics`), split them into separate, sequential atomic commits rather than one giant dump.

## 3. Commit Message Format
Follow strict **Conventional Commits** with mandatory monorepo scope:

```text
<type>(<scope>): <concise summary in imperative mood, max 72 chars>

- <Technical 1: and bullet changed what why>
- <Technical 2: architectural bullet detail mathematical or>
```

### Valid `<type>` values:
- `feat`: New quantitative model, API connector, ORM schema, or regulatory rule.
- `fix`: Bugfix in math, schema, or data pipeline.
- `test`: Adding or refactoring unit/integration tests.
- `refactor`: Code restructuring without changing external behavior.
- `build` / `chore`: Workspace config (`pyproject.toml`, `uv.lock`, skills, linters).
- `docs`: `README.md`, architecture diagrams, docstrings.

### Valid `<scope>` values:
- `data-core` (for `packages/risk-data-core`)
- `pricing-engine` (for `packages/portfolio-pricing-engine`)
- `cmf-r07` (for `packages/cmf-regulatory-r07`)
- `risk-analytics` (for `packages/market-risk-analytics`)
- `workspace` (for root `pyproject.toml`, `AGENTS.md`, or `.opencode/skills`)
