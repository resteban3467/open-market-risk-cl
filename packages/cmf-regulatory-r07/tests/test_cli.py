"""Prueba de humo del CLI completo (enriquecimiento -> GIRR -> APRM)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cmf_regulatory_r07.cli import main
from risk_data_core import RiskDatabase


def _seed(db_path: Path) -> None:
    writer = RiskDatabase(db_path, read_only=False)
    try:
        writer.init_schema()
        writer.execute(
            "INSERT INTO dim_trades VALUES (?, ?, ?, ?, ?, ?)",
            ["t1", "BOND", "TRADING", date(2025, 1, 1), date(2028, 1, 1), "Treasury"],
        )
        writer.execute(
            "INSERT INTO dim_legs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "l1",
                "t1",
                "ACTIVE",
                "CLP",
                1_000_000_000.0,
                "FIXED",
                "ACT/365",
                "CONTINUOUS",
                0.05,
                "ANNUAL",
                None,
            ],
        )
        writer.execute(
            "INSERT INTO fct_valued_cashflows VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                date(2026, 1, 1),
                "cf1",
                "t1",
                "l1",
                date(2028, 1, 1),
                2.0,
                1_000_000_000.0,
                0.05,
                0.05,
                1_050_000_000.0,
                0.9,
                1_000_000_000.0,
                1_000_000_000.0,
                -100.0,
                -100.0,
                2.0,
            ],
        )
        writer.execute(
            "INSERT INTO fct_fx_rates VALUES (?, ?, ?, ?)",
            [date(2026, 1, 1), "USD/CLP", 950.0, "BCCh"],
        )
    finally:
        writer.close()


def test_cli_prints_summary(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli.duckdb"
    _seed(db_path)

    exit_code = main(["--date", "2026-01-01", "--db", str(db_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "R07" in output
    assert "APRM total" in output


def test_cli_exports_json_report(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.duckdb"
    output_path = tmp_path / "report.json"
    _seed(db_path)

    exit_code = main(["--date", "2026-01-01", "--db", str(db_path), "--output", str(output_path)])

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["seccion"] == "s02"
    assert report["valuation_date"] == "2026-01-01"
    assert report["aprm"]["total_aprm"] > 0
    assert len(report["r07_s02"]) == 1
