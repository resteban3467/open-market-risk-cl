from datetime import date
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from pandera.errors import SchemaError
from risk_data_core import RiskDatabase
from risk_data_core.models import Trade
from risk_data_core.schemas import (
    DayCountConvention,
    FxRateContract,
    LegContract,
    ValuedCashflowContract,
    ValuedCashflowRelaxedContract,
)


def _fx_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "currency_pair": ["USD/CLP"],
            "spot_rate": [900.25],
            "source": ["test"],
        }
    )


def _valued_cashflow_contract() -> ValuedCashflowContract:
    return ValuedCashflowContract(
        valuation_date=date(2026, 1, 2),
        cashflow_id="cf-1",
        trade_id="trade-1",
        leg_id="leg-1",
        payment_date=date(2027, 1, 2),
        yf=1.0,
        nominal_amount=1_000_000.0,
        coupon_rate=0.045,
        projected_rate=0.045,
        total_cashflow=45_000.0,
        discount_factor=0.956,
        present_value_orig_ccy=43_020.0,
        present_value_clp=43_020.0,
        dv01_orig_ccy=-0.41,
        dv01_clp=-0.41,
        modified_duration=0.95,
    )


def test_in_memory_schema_and_zero_copy_bulk_roundtrip() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        db.bulk_insert_polars(_fx_frame(), "fct_fx_rates")

        result = db.query_polars(
            "SELECT valuation_date, currency_pair, spot_rate, source "
            "FROM fct_fx_rates ORDER BY valuation_date"
        )

        assert result.schema == _fx_frame().schema
        assert result.to_dicts() == _fx_frame().to_dicts()
    finally:
        db.close()


def test_bulk_insert_replaces_a_valuation_partition_idempotently() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        first = _fx_frame()
        corrected = first.with_columns(pl.lit(901.5).alias("spot_rate"))

        db.bulk_insert_polars(first, "fct_fx_rates", "valuation_date", date(2026, 1, 2))
        db.bulk_insert_polars(corrected, "fct_fx_rates", "valuation_date", date(2026, 1, 2))

        result = db.query_polars("SELECT * FROM fct_fx_rates")
        assert result.height == 1
        assert result["spot_rate"][0] == pytest.approx(901.5)
    finally:
        db.close()


def test_bulk_insert_validates_table_columns() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        incomplete = _fx_frame().drop("source")

        with pytest.raises(ValueError, match=r"missing columns.*source"):
            db.bulk_insert_polars(incomplete, "fct_fx_rates")
    finally:
        db.close()


def test_read_only_mode_blocks_schema_bulk_and_orm_writes() -> None:
    db = RiskDatabase(Path(":memory:"))
    try:
        assert db.read_only is True
        with pytest.raises(PermissionError, match="read_only"):
            db.init_schema()
        with pytest.raises(PermissionError, match="read_only"):
            db.bulk_insert_polars(_fx_frame(), "fct_fx_rates")

        with db.session_factory() as session:
            session.add(
                Trade(
                    trade_id="trade-1",
                    instrument_type="BOND",
                    book="BANKING",
                    trade_date=date(2026, 1, 2),
                )
            )
            with pytest.raises(PermissionError, match="read_only"):
                session.commit()
    finally:
        db.close()


def test_empty_dataframe_is_a_noop_and_empty_query_has_schema() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        empty = pl.DataFrame(schema=_fx_frame().schema)
        db.bulk_insert_polars(empty, "fct_fx_rates")
        result = db.query_polars("SELECT * FROM fct_fx_rates")

        assert result.is_empty()
        assert result.schema == _fx_frame().schema
    finally:
        db.close()


def test_query_api_rejects_writes_in_read_only_memory_mode() -> None:
    db = RiskDatabase(Path(":memory:"))
    try:
        with pytest.raises(PermissionError, match="read_only"):
            db.query_polars("CREATE TABLE forbidden (id INTEGER)")
    finally:
        db.close()


def test_contracts_validate_conventions_and_coupon_rate() -> None:
    leg = LegContract(
        leg_id="leg-1",
        trade_id="trade-1",
        leg_type="ACTIVE",
        currency="CLP",
        rate_type="FIXED",
        day_count_convention="ACT/365",
        compounding_convention="CONTINUOUS",
        coupon_rate=0.045,
    )
    assert leg.coupon_rate == pytest.approx(0.045)

    with pytest.raises(ValueError, match="day_count_convention"):
        LegContract(
            leg_id="leg-2",
            trade_id="trade-1",
            leg_type="ACTIVE",
            currency="CLP",
            rate_type="FIXED",
            day_count_convention=cast(DayCountConvention, "ACT/ACT"),
            compounding_convention="CONTINUOUS",
            coupon_rate=0.0,
        )

    with pytest.raises(ValueError, match="spot_rate"):
        FxRateContract(
            valuation_date=date(2026, 1, 2),
            currency_pair="USD/CLP",
            spot_rate=0.0,
            source="test",
        )


def test_orm_contract_tables_can_be_written_in_read_write_mode() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with db.session_factory() as session:
            session.add(
                Trade(
                    trade_id="trade-1",
                    instrument_type="BOND",
                    book="BANKING",
                    trade_date=date(2026, 1, 2),
                )
            )
            session.commit()
        assert db.query_polars("SELECT trade_id FROM dim_trades")["trade_id"].to_list() == [
            "trade-1"
        ]
    finally:
        db.close()


def test_bulk_insert_rejects_unsafe_table_names() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with pytest.raises(ValueError, match="invalid SQL identifier"):
            db.bulk_insert_polars(_fx_frame(), "fct_fx_rates; DROP TABLE dim_trades")
    finally:
        db.close()


def test_bulk_insert_is_idempotent_across_three_runs() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        frame = _fx_frame()

        for _ in range(3):
            db.bulk_insert_polars(frame, "fct_fx_rates")

        result = db.query_polars("SELECT * FROM fct_fx_rates ORDER BY valuation_date")
        assert result.height == 1
        assert result.to_dicts() == frame.to_dicts()
    finally:
        db.close()


def test_upsert_updates_corrected_row_without_duplicating() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        first = _fx_frame()
        corrected = first.with_columns(pl.lit(901.5).alias("spot_rate"))

        db.bulk_insert_polars(first, "fct_fx_rates")
        db.bulk_insert_polars(corrected, "fct_fx_rates")

        result = db.query_polars("SELECT * FROM fct_fx_rates")
        assert result.height == 1
        assert result["spot_rate"][0] == pytest.approx(901.5)
    finally:
        db.close()


def test_bulk_insert_rejects_invalid_contract_before_writing() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        invalid = _fx_frame().with_columns(pl.lit(0.0).alias("spot_rate"))

        with pytest.raises(SchemaError):
            db.bulk_insert_polars(invalid, "fct_fx_rates")

        assert db.query_polars("SELECT * FROM fct_fx_rates").is_empty()
    finally:
        db.close()


def test_conflict_columns_and_partition_replacement_are_mutually_exclusive() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with pytest.raises(ValueError, match="mutually exclusive"):
            db.bulk_insert_polars(
                _fx_frame(),
                "fct_fx_rates",
                replace_partition_col="valuation_date",
                replace_partition_val=date(2026, 1, 2),
                conflict_columns=("valuation_date", "currency_pair"),
            )
    finally:
        db.close()


def test_valued_cashflows_validation_and_triple_run_idempotency() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        frame = pl.DataFrame(
            {
                "valuation_date": [date(2026, 1, 2)],
                "cashflow_id": ["cf-1"],
                "trade_id": ["trade-1"],
                "leg_id": ["leg-1"],
                "payment_date": [date(2027, 1, 2)],
                "yf": [1.0],
                "nominal_amount": [1_000_000.0],
                "coupon_rate": [0.045],
                "projected_rate": [0.045],
                "total_cashflow": [45_000.0],
                "discount_factor": [0.9560],
                "present_value_orig_ccy": [43_020.0],
                "present_value_clp": [43_020.0],
                "dv01_orig_ccy": [-0.41],
                "dv01_clp": [-0.41],
                "modified_duration": [0.95],
            }
        )

        for _ in range(3):
            db.bulk_insert_polars(frame, "fct_valued_cashflows")

        result = db.query_polars("SELECT * FROM fct_valued_cashflows")
        assert result.height == 1
        assert result.to_dicts() == frame.to_dicts()
    finally:
        db.close()


def test_valued_cashflow_contract_enforces_pandera_ranges() -> None:
    base = _valued_cashflow_contract()
    assert base.coupon_rate == pytest.approx(0.045)
    assert base.discount_factor == pytest.approx(0.956)

    for field_name, bad_value in (
        ("coupon_rate", -0.0025),
        ("discount_factor", 0.0),
        ("discount_factor", 1.0001),
    ):
        payload = base.model_dump()
        payload[field_name] = bad_value
        with pytest.raises(ValueError, match=field_name):
            ValuedCashflowContract(**payload)


def test_relaxed_cashflow_contract_allows_negative_coupon_explicitly() -> None:
    payload = _valued_cashflow_contract().model_dump()
    payload["coupon_rate"] = -0.0025

    relaxed = ValuedCashflowRelaxedContract(**payload)

    assert relaxed.coupon_rate == pytest.approx(-0.0025)
