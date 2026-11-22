"""CLI del calculo regulatorio CMF RAN 21-7 / Archivo R07.

Uso::

    uv run report-r07 --date 2026-10-30 [--db data/market_risk.duckdb] [--output report.json]

Ejecuta el pipeline completo (enriquecimiento del portafolio -> ``r07_s02`` ->
GIRR -> APRM), imprime el resumen por componente y el APRM total en CLP y,
opcionalmente, exporta el reporte estructurado.
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import polars as pl
from risk_data_core.observability import get_logger, timed

from cmf_regulatory_r07.config.scope import BOOKS_TASAS
from cmf_regulatory_r07.domain.aprm_aggregator import aggregate_aprm
from cmf_regulatory_r07.domain.girr_engine import compute_girr
from cmf_regulatory_r07.domain.portfolio_enrichment import build_r07_s02
from cmf_regulatory_r07.r07_exporter import build_report, export_report, format_summary
from cmf_regulatory_r07.repository import RegulatoryDataRepository

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_DB = _REPO_ROOT / "data" / "market_risk.duckdb"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="report-r07",
        description="Calcula el APRM de riesgo general de tasas (RAN 21-7 / R07).",
    )
    parser.add_argument("--date", required=True, help="Fecha de valorizacion (YYYY-MM-DD).")
    parser.add_argument(
        "--db",
        default=os.environ.get("MARKET_RISK_DB", str(_DEFAULT_DB)),
        help="Ruta a la base DuckDB (default: data/market_risk.duckdb).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta de salida del reporte (.json completo o .csv del detalle r07_s02).",
    )
    return parser.parse_args(argv)


def _build_fx_factors(fx_rates: pl.DataFrame, market_factors: pl.DataFrame) -> dict[str, float]:
    """Factores de conversion a CLP por moneda usando tasas BCCh."""
    factors: dict[str, float] = {"CLP": 1.0}
    for row in market_factors.iter_rows(named=True):
        if row["factor_code"] == "UF":
            factors["UF"] = float(row["value"])
    for row in fx_rates.iter_rows(named=True):
        pair = str(row["currency_pair"]).upper()
        base, _, quote = pair.partition("/")
        spot = float(row["spot_rate"])
        if quote == "CLP":
            factors[base] = spot
        elif base == "CLP":
            factors[quote] = 1.0 / spot
    return factors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    valuation_date = date.fromisoformat(args.date)
    logger = get_logger("cmf_regulatory_r07.cli")
    repository = RegulatoryDataRepository.from_path(Path(args.db))
    try:
        with timed(logger, "run_r07", valuation_date=valuation_date.isoformat()):
            cashflows = repository.load_cashflows(valuation_date)
            if cashflows.is_empty():
                logger.warning("no_cashflows", valuation_date=valuation_date.isoformat())
            trades = repository.load_trades(books=BOOKS_TASAS)
            legs = repository.load_legs()
            fx_rates = repository.load_fx_rates(valuation_date)
            market_factors = repository.load_market_factors(valuation_date, ["UF"])
            fx_factors = _build_fx_factors(fx_rates, market_factors)

            r07_s02 = build_r07_s02(
                trades=trades,
                legs=legs,
                cashflows=cashflows,
                fx_factors=fx_factors,
                reference_date=valuation_date,
                books=BOOKS_TASAS,
            )
            girr = compute_girr(r07_s02)
            aprm = aggregate_aprm({"riesgo_general_tasas": girr.total_charge})
            report = build_report(
                valuation_date=valuation_date,
                girr=girr,
                aprm=aprm,
                r07_s02=r07_s02,
            )
            if args.output:
                export_report(report, args.output)
            logger.info(
                "r07_done",
                valuation_date=valuation_date.isoformat(),
                books=BOOKS_TASAS,
                rows=r07_s02.height,
                cargo_riesgo_general=girr.total_charge,
                aprm_total=aprm.total_aprm,
            )
        print(format_summary(report))
        return 0
    finally:
        repository.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
