"""Formateo y exportacion del reporte estructurado equivalente al Archivo R07.

Esta iteracion produce la seccion 02 del R07 (riesgo general de tasas de
interes). El reporte consolida el detalle enriquecido ``r07_s02``, el desglose
GIRR y el APRM total en CLP.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
from risk_data_core.observability import get_logger, timed

from cmf_regulatory_r07.domain.aprm_aggregator import AprmResult
from cmf_regulatory_r07.domain.girr_engine import GirrResult


def build_report(
    *,
    valuation_date: date,
    girr: GirrResult,
    aprm: AprmResult,
    r07_s02: pl.DataFrame,
) -> dict[str, Any]:
    """Construye el reporte estructurado (dict serializable a JSON)."""
    return {
        "report": "R07",
        "seccion": "s02",
        "concepto": "riesgo_general_tasas_interes",
        "valuation_date": valuation_date.isoformat(),
        "moneda_reporte": "CLP",
        "girr": girr.as_dict(),
        "aprm": aprm.as_dict(),
        "r07_s02": r07_s02.to_dicts(),
    }


def export_report(report: dict[str, Any], output_path: str | Path) -> Path:
    """Escribe el reporte en ``output_path`` (``.csv`` -> detalle; otro -> JSON)."""
    logger = get_logger(__name__)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame = pl.DataFrame(report["r07_s02"])
        with timed(logger, "export_r07_csv", output=str(path), rows=frame.height):
            frame.write_csv(path)
    else:
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        with timed(logger, "export_r07_json", output=str(path)):
            path.write_text(payload, encoding="utf-8")
    return path


def format_summary(report: dict[str, Any]) -> str:
    """Resumen legible para la salida de consola del CLI."""
    girr = report["girr"]
    aprm = report["aprm"]
    lines = [
        "R07 - Riesgo general de tasas de interes (seccion 02)",
        f"Fecha de valorizacion : {report['valuation_date']}",
        f"Moneda de reporte     : {report['moneda_reporte']}",
        "",
        f"Posicion neta ponderada : {girr['net_weighted_position']:>24,.2f}",
        f"Ajuste vertical         : {girr['vertical']:>24,.2f}",
        f"Ajustes horizontales    : {girr['horizontal']:>24,.2f}",
        f"Cargo riesgo general    : {girr['total_charge']:>24,.2f}",
        "",
    ]
    for component in aprm["components"]:
        lines.append(
            f"  {component['name']:<24}: cargo {component['charge']:>18,.2f} "
            f"| APRM {component['aprm']:>18,.2f}"
        )
    lines.extend(
        [
            "",
            f"APRM total (x{aprm['multiplier']:g}) : {aprm['total_aprm']:>24,.2f} CLP",
        ]
    )
    return "\n".join(lines)


__all__ = ["build_report", "export_report", "format_summary"]
