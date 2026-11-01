from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from risk_data_core import RiskDatabase
from risk_data_core.observability import get_logger, timed
from structlog.testing import capture_logs


def _fx_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "currency_pair": ["USD/CLP"],
            "spot_rate": [900.25],
            "source": ["test"],
        }
    )


def test_get_logger_returns_a_structlog_logger() -> None:
    assert get_logger("test.observability") is not None


def test_timed_emits_execution_time_ms_and_context() -> None:
    logger = get_logger("test.timed")
    with (
        capture_logs() as logs,
        timed(logger, "price_portfolio", valuation_date="2026-01-02", trade_count=128),
    ):
        time.sleep(0.001)

    assert len(logs) == 1
    trace = logs[0]
    assert trace["event"] == "price_portfolio"
    assert trace["valuation_date"] == "2026-01-02"
    assert trace["trade_count"] == 128
    assert trace["execution_time_ms"] >= 0.0
    assert isinstance(trace["execution_time_ms"], float)


def test_timed_emits_a_trace_when_the_block_raises() -> None:
    logger = get_logger("test.timed.raises")
    with (
        capture_logs() as logs,
        pytest.raises(RuntimeError, match="boom"),
        timed(logger, "failing_operation", trade_count=1),
    ):
        raise RuntimeError("boom")

    assert len(logs) == 1
    assert logs[0]["event"] == "failing_operation"
    assert logs[0]["trade_count"] == 1
    assert "execution_time_ms" in logs[0]


def test_query_polars_emits_a_structured_trace() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with capture_logs() as logs:
            db.query_polars("SELECT 1 AS one")

        trace = next(entry for entry in logs if entry["event"] == "query_polars")
        assert trace["operation"] == "query"
        assert trace["execution_time_ms"] >= 0.0
    finally:
        db.close()


def test_bulk_insert_emits_row_count_and_table_name() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with capture_logs() as logs:
            db.bulk_insert_polars(_fx_frame(), "fct_fx_rates")

        trace = next(entry for entry in logs if entry["event"] == "bulk_insert_polars")
        assert trace["table_name"] == "fct_fx_rates"
        assert trace["row_count"] == 1
        assert trace["execution_time_ms"] >= 0.0
    finally:
        db.close()


def test_bulk_insert_emits_a_trace_for_an_empty_dataframe() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        empty = pl.DataFrame(schema=_fx_frame().schema)

        with capture_logs() as logs:
            db.bulk_insert_polars(empty, "fct_fx_rates")

        trace = next(entry for entry in logs if entry["event"] == "bulk_insert_polars")
        assert trace["table_name"] == "fct_fx_rates"
        assert trace["row_count"] == 0
        assert trace["skipped_reason"] == "empty_dataframe"
        assert trace["execution_time_ms"] == 0.0
    finally:
        db.close()
