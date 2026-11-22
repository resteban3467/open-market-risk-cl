"""Pruebas de solo lectura del repositorio regulatorio y bloqueo de escrituras."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from cmf_regulatory_r07.repository import RegulatoryDataRepository
from risk_data_core import RiskDatabase


def test_repository_rejects_read_write_database() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        with pytest.raises(ValueError, match="read_only"):
            RegulatoryDataRepository(db)
    finally:
        db.close()


def test_read_only_database_blocks_writes() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=True)
    try:
        assert db.read_only is True
        with pytest.raises(PermissionError, match="read_only"):
            db.init_schema()
        with pytest.raises(PermissionError, match="read_only"):
            db.query_polars("CREATE TABLE forbidden (id INTEGER)")
        with pytest.raises(PermissionError, match="read_only"):
            db.execute("DELETE FROM fct_fx_rates")
        with pytest.raises(PermissionError, match="read_only"):
            db.bulk_insert_polars(
                pl.DataFrame(
                    {
                        "valuation_date": [date(2026, 1, 2)],
                        "currency_pair": ["USD/CLP"],
                        "spot_rate": [900.0],
                        "source": ["BCCh"],
                    }
                ),
                "fct_fx_rates",
            )
    finally:
        db.close()


def _seed_database(db_path: Path) -> None:
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
                1_000_000.0,
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
                date(2027, 1, 1),
                1.0,
                1_000_000.0,
                0.05,
                0.05,
                1_050_000.0,
                0.95,
                1_000_000.0,
                1_000_000.0,
                -10.0,
                -10.0,
                1.0,
            ],
        )
        writer.execute(
            "INSERT INTO fct_fx_rates VALUES (?, ?, ?, ?)",
            [date(2026, 1, 1), "USD/CLP", 950.0, "BCCh"],
        )
    finally:
        writer.close()


def test_repository_reads_seeded_database(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    _seed_database(db_path)

    repository = RegulatoryDataRepository.from_path(db_path)
    try:
        assert repository.load_cashflows(date(2026, 1, 1)).height == 1
        assert repository.load_trades().height == 1
        assert repository.load_legs().height == 1
        fx = repository.load_fx_rates(date(2026, 1, 1))
        assert fx.height == 1
        assert fx["spot_rate"][0] == pytest.approx(950.0)
    finally:
        repository.close()


def test_repository_cannot_write_through_its_database(tmp_path: Path) -> None:
    db_path = tmp_path / "ro.duckdb"
    _seed_database(db_path)

    repository = RegulatoryDataRepository.from_path(db_path)
    try:
        with pytest.raises(PermissionError, match="read_only"):
            repository.db.execute("DELETE FROM dim_trades")
    finally:
        repository.close()
