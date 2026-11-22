"""CLI entry point for the bulk market-data ingestion engine.

Usage::

    uv run ingest [--series usd_clp vix] [--start 2020-01-01] [--end 2026-01-02] [--full]
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path
from typing import cast

import polars as pl
from risk_data_core import RiskDatabase
from risk_data_core.observability import get_logger

from portfolio_pricing_engine.adapters.bcch_adapter import BcchAdapter
from portfolio_pricing_engine.adapters.duckdb_trade_repository import DuckDBTradeRepository
from portfolio_pricing_engine.adapters.fred_adapter import FredAdapter
from portfolio_pricing_engine.config import load_catalog
from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve
from portfolio_pricing_engine.domain.ingestion_engine import IngestionEngine
from portfolio_pricing_engine.domain.portfolio_loader import PortfolioLoader

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CATALOG = (
    _REPO_ROOT / "packages" / "portfolio-pricing-engine" / "config" / "series_catalog.yaml"
)
_DEFAULT_DB = _REPO_ROOT / "data" / "market_risk.duckdb"


def _load_dotenv(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` pairs from ``.env`` without overriding real env vars."""
    env_path = path or (_REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Bulk, idempotent market-data ingestion (BCCh / FRED) into DuckDB.",
    )
    parser.add_argument(
        "--catalog",
        default=os.environ.get("SERIES_CATALOG", str(_DEFAULT_CATALOG)),
        help="Path to the series catalog YAML (default: repo catalog).",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MARKET_RISK_DB", str(_DEFAULT_DB)),
        help="Path to the DuckDB database (default: data/market_risk.duckdb).",
    )
    parser.add_argument(
        "--series",
        nargs="*",
        default=None,
        help="Series ids to ingest (default: every catalog entry).",
    )
    parser.add_argument("--start", help="Override the backfill start date (YYYY-MM-DD).")
    parser.add_argument("--end", help="Override the end date (YYYY-MM-DD, default: today).")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Refetch from each series' start_date, ignoring existing rows.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_dotenv()
    logger = get_logger("portfolio_pricing_engine.cli")

    catalog = load_catalog(args.catalog)
    db = RiskDatabase(Path(args.db), read_only=False)
    try:
        db.init_schema()
        engine = IngestionEngine(
            db,
            catalog,
            sources={"bcch": BcchAdapter(), "fred": FredAdapter()},
        )
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        series_ids = set(args.series) if args.series else None

        results = engine.ingest(series_ids, start=start, end=end, full=args.full, fail_fast=False)
        for result in results:
            logger.info(
                "ingest_result",
                series_id=result.series_id,
                adapter=result.adapter,
                status=result.status,
                rows=result.rows,
                start=result.start.isoformat(),
                end=result.end.isoformat(),
            )
        return 0
    finally:
        db.close()


__all__ = ["eod_main", "main"]


def _parse_eod_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eod",
        description="Value the portfolio for a date using curves/FX from DuckDB.",
    )
    parser.add_argument("--date", required=True, help="Valuation date (YYYY-MM-DD).")
    parser.add_argument(
        "--db",
        default=os.environ.get("MARKET_RISK_DB", str(_DEFAULT_DB)),
        help="Path to the DuckDB database (default: data/market_risk.duckdb).",
    )
    parser.add_argument("--curve", default="CLP_BCP", help="Curve name in fct_market_curves.")
    return parser.parse_args(argv)


def _load_zero_curve(db: RiskDatabase, name: str, valuation_date: date) -> ZeroCurve | None:
    """Load the latest zero curve ``<=`` ``valuation_date`` for ``name``."""
    frame = db.query_polars("SELECT * FROM fct_market_curves")
    nodes = frame.filter(
        (pl.col("curve_name") == name) & (pl.col("valuation_date") <= valuation_date)
    )
    if nodes.is_empty():
        return None
    latest = nodes["valuation_date"].max()
    points = (
        nodes.filter(pl.col("valuation_date") == latest)
        .select(["tenor_yf", "zero_rate"])
        .sort("tenor_yf")
    )
    return ZeroCurve.from_zero_rates(points, cast(date, latest))


def _latest_factor_map(db: RiskDatabase, valuation_date: date) -> dict[str, float]:
    frame = db.query_polars("SELECT * FROM fct_market_factors")
    latest = (
        frame.filter(pl.col("valuation_date") <= valuation_date)
        .sort("valuation_date", descending=True)
        .unique(subset=["factor_code"], keep="first")
    )
    return {
        row["factor_code"]: float(row["value"])
        for row in latest.select(["factor_code", "value"]).iter_rows(named=True)
    }


def _latest_fx_map(db: RiskDatabase, valuation_date: date) -> dict[str, float]:
    frame = db.query_polars("SELECT * FROM fct_fx_rates")
    latest = (
        frame.filter(pl.col("valuation_date") <= valuation_date)
        .sort("valuation_date", descending=True)
        .unique(subset=["currency_pair"], keep="first")
    )
    return {
        row["currency_pair"]: float(row["spot_rate"])
        for row in latest.select(["currency_pair", "spot_rate"]).iter_rows(named=True)
    }


def eod_main(argv: list[str] | None = None) -> int:
    """Run the end-of-day valuation reading curves, UF and FX from DuckDB."""
    args = _parse_eod_args(argv)
    _load_dotenv()
    logger = get_logger("portfolio_pricing_engine.cli.eod")
    valuation_date = date.fromisoformat(args.date)
    db = RiskDatabase(Path(args.db), read_only=False)
    try:
        db.init_schema()
        curves: dict[str, ZeroCurve] = {}
        for currency, name in (("CLP", args.curve), ("UF", "CLP_BCU"), ("USD", "USD_TREASURY")):
            loaded = _load_zero_curve(db, name, valuation_date)
            if loaded is not None:
                curves[currency] = loaded
        if "CLP" not in curves:
            msg = f"no curve {args.curve!r} for {valuation_date.isoformat()}"
            raise SystemExit(msg)

        factor_map = _latest_factor_map(db, valuation_date)
        fx_map = _latest_fx_map(db, valuation_date)

        factors: dict[str, float] = {"CLP": 1.0}
        if "UF" in factor_map:
            factors["UF"] = factor_map["UF"]
        if "USD/CLP" in fx_map:
            factors["USD"] = fx_map["USD/CLP"]

        tib = factor_map.get("TIB_1D", 4.5) / 100.0
        floating_rates = {
            "SOFR": factor_map.get("SOFR", 3.92) / 100.0,
            "TIB": tib,
            "TPM": factor_map.get("TPM", 4.5) / 100.0,
            "ICP": tib,
            "UF": 0.02,
        }

        loader = PortfolioLoader(db, DuckDBTradeRepository(db))
        valued = loader.run_eod(
            valuation_date,
            args.curve,
            curves=curves,
            conversion_factors=factors,
            floating_rates=floating_rates,
        )
        logger.info("eod_done", valuation_date=valuation_date.isoformat(), rows=valued.height)
        return 0
    finally:
        db.close()
