"""CLI entry point for the bulk market-data ingestion engine.

Usage::

    uv run ingest [--series usd_clp vix] [--start 2020-01-01] [--end 2026-01-02] [--full]
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

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


def eod_main(argv: list[str] | None = None) -> int:
    """Run the end-of-day valuation reading curves, UF and FX from DuckDB."""
    args = _parse_eod_args(argv)
    _load_dotenv()
    logger = get_logger("portfolio_pricing_engine.cli.eod")
    valuation_date = date.fromisoformat(args.date)
    db = RiskDatabase(Path(args.db), read_only=False)
    try:
        db.init_schema()
        curve_df = db.query_polars(
            "SELECT tenor_yf, zero_rate FROM fct_market_curves "
            "WHERE valuation_date = ? AND curve_name = ? ORDER BY tenor_yf",
            [valuation_date, args.curve],
        )
        if curve_df.is_empty():
            msg = f"no curve {args.curve!r} for {valuation_date.isoformat()}"
            raise SystemExit(msg)
        curve = ZeroCurve.from_zero_rates(curve_df, valuation_date)

        factors: dict[str, float] = {"CLP": 1.0}
        uf = db.query_polars(
            "SELECT value FROM fct_market_factors WHERE factor_code = 'UF' "
            "AND valuation_date <= ? ORDER BY valuation_date DESC LIMIT 1",
            [valuation_date],
        )
        if not uf.is_empty():
            factors["UF"] = float(uf["value"][0])
        fx = db.query_polars(
            "SELECT spot_rate FROM fct_fx_rates WHERE currency_pair = 'USD/CLP' "
            "AND valuation_date <= ? ORDER BY valuation_date DESC LIMIT 1",
            [valuation_date],
        )
        if not fx.is_empty():
            factors["USD"] = float(fx["spot_rate"][0])

        loader = PortfolioLoader(db, DuckDBTradeRepository(db))
        valued = loader.run_eod(
            valuation_date,
            args.curve,
            curve=curve,
            conversion_factors=factors,
        )
        logger.info("eod_done", valuation_date=valuation_date.isoformat(), rows=valued.height)
        return 0
    finally:
        db.close()
