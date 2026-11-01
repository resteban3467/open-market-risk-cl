"""Architecture boundaries (Ports & Adapters) for the risk workspace.

Enforces the ``clean-architecture-standards`` skill at import level:

- Analytical packages (``market-risk-analytics``, ``cmf-regulatory-r07``) MUST
  NOT import ingestion logic from ``portfolio-pricing-engine``.
- The shared kernel (``risk-data-core``) MUST NOT import any downstream package.
- ``portfolio-pricing-engine`` may only depend on ``risk-data-core``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

# Importable package name -> repository directory name.
PACKAGE_DIRS = {
    "risk_data_core": "risk-data-core",
    "portfolio_pricing_engine": "portfolio-pricing-engine",
    "market_risk_analytics": "market-risk-analytics",
    "cmf_regulatory_r07": "cmf-regulatory-r07",
}
FIRST_PARTY = frozenset(PACKAGE_DIRS)


def _workspace_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    msg = "workspace root not found"
    raise RuntimeError(msg)


PACKAGES_DIR = _workspace_root() / "packages"


def _module_files(import_name: str) -> Iterator[Path]:
    src = PACKAGES_DIR / PACKAGE_DIRS[import_name] / "src"
    yield from sorted(src.rglob("*.py"))


def _first_party_imports(path: Path) -> set[str]:
    """Return the first-party package names imported by ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names & FIRST_PARTY


def _illegal_imports(import_name: str, allowed: set[str]) -> dict[str, set[str]]:
    violations: dict[str, set[str]] = {}
    for path in _module_files(import_name):
        illegal = _first_party_imports(path) - allowed - {import_name}
        if illegal:
            violations[str(path.relative_to(PACKAGES_DIR))] = illegal
    return violations


def test_shared_kernel_has_no_downstream_dependencies() -> None:
    violations = _illegal_imports("risk_data_core", set())
    assert not violations, f"risk-data-core must not import downstream packages: {violations}"


def test_analytical_packages_do_not_import_ingestion() -> None:
    for package in ("market_risk_analytics", "cmf_regulatory_r07"):
        violations = _illegal_imports(package, {"risk_data_core"})
        assert not violations, f"{package} must not import ingestion logic: {violations}"


def test_pricing_engine_only_depends_on_the_kernel() -> None:
    violations = _illegal_imports("portfolio_pricing_engine", {"risk_data_core"})
    assert not violations, f"pricing engine may only import risk-data-core: {violations}"
