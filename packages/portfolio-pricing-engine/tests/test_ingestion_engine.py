"""Integration tests for the parametrized ingestion engine (in-memory DuckDB)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from pandera.errors import SchemaError
from portfolio_pricing_engine.config import SeriesCatalog, SeriesEntry
from portfolio_pricing_engine.domain.bootstrapping import tenor_to_years
from portfolio_pricing_engine.domain.ingestion_engine import IngestionEngine
from risk_data_core import RiskDatabase, SantiagoCalendar
from risk_data_core.validation import SCHEMAS

_EMPTY_SERIES_SCHEMA = {"as_of_date": pl.Date, "value": pl.Float64}
_EMPTY_CURVE_SCHEMA = {
    "valuation_date": pl.Date,
    "tenor_label": pl.String,
    "tenor_yf": pl.Float64,
    "par_rate": pl.Float64,
}


class FakeSource:
    """In-memory ``SeriesHistoryPort`` mirroring the BCCh/FRED adapters."""

    def __init__(self, series: dict[str, list[tuple[date, float]]] | None = None) -> None:
        self.series = series or {}

    def fetch_series_history(self, code: str, start: date, end: date) -> pl.DataFrame:
        rows = [(d, v) for d, v in self.series.get(code, []) if start <= d <= end]
        if not rows:
            return pl.DataFrame(schema=_EMPTY_SERIES_SCHEMA)
        return pl.DataFrame(
            {"as_of_date": [d for d, _ in rows], "value": [v for _, v in rows]}
        ).sort("as_of_date")

    def fetch_curve_history(self, codes: dict[str, str], start: date, end: date) -> pl.DataFrame:
        frames: list[pl.DataFrame] = []
        for tenor, code in codes.items():
            history = self.fetch_series_history(code, start, end)
            if history.is_empty():
                continue
            frames.append(
                history.rename({"as_of_date": "valuation_date", "value": "par_rate"}).with_columns(
                    [
                        pl.lit(tenor).alias("tenor_label"),
                        pl.lit(tenor_to_years(tenor)).alias("tenor_yf"),
                    ]
                )
            )
        if not frames:
            return pl.DataFrame(schema=_EMPTY_CURVE_SCHEMA)
        return pl.concat(frames).with_columns((pl.col("par_rate") / 100.0).alias("par_rate"))


def _db() -> RiskDatabase:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    db.init_schema()
    return db


def _fx_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="usd_clp",
        source="bcch",
        adapter="fx",
        target_table="fct_fx_rates",
        frequency="DAILY",
        code="TCO",
        currency_pair="USD/CLP",
        start_date=date(2026, 1, 1),
    )


def _factor_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="vix",
        source="fred",
        adapter="factor",
        target_table="fct_market_factors",
        frequency="DAILY",
        code="VIXCLS",
        factor_code="VIX",
        start_date=date(2026, 1, 1),
    )


def _monthly_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="ipc",
        source="bcch",
        adapter="factor",
        target_table="fct_market_factors",
        frequency="MONTHLY",
        code="IPC",
        factor_code="IPC",
        start_date=date(2026, 1, 1),
    )


def _curve_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="clp_bcp",
        source="bcch",
        adapter="curve",
        target_table="fct_market_curves",
        frequency="DAILY",
        curve_name="CLP_BCP",
        bootstrap=True,
        codes={"2Y": "BCP2", "5Y": "BCP5", "10Y": "BCP10"},
        start_date=date(2026, 1, 1),
    )


def test_engine_ingests_fx_idempotently() -> None:
    db = _db()
    source = FakeSource({"TCO": [(date(2026, 1, 2), 900.25), (date(2026, 1, 5), 910.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_fx_entry()]), {"bcch": source})

    results = engine.ingest(full=True)
    assert results[0].rows == 2

    engine.ingest(full=True)
    engine.ingest(full=True)
    frame = db.query_polars("SELECT * FROM fct_fx_rates ORDER BY valuation_date")
    assert frame.height == 2
    assert frame["spot_rate"].to_list() == pytest.approx([900.25, 910.0])
    db.close()


def test_engine_rejects_non_positive_fx_before_writing() -> None:
    db = _db()
    source = FakeSource({"TCO": [(date(2026, 1, 2), 0.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_fx_entry()]), {"bcch": source})

    with pytest.raises(SchemaError):
        engine.ingest(full=True)
    count = db.query_polars("SELECT COUNT(*) AS n FROM fct_fx_rates")
    assert count["n"][0] == 0
    db.close()


def test_engine_ingests_daily_factor_into_staging_and_fact() -> None:
    db = _db()
    source = FakeSource({"VIXCLS": [(date(2026, 1, 2), 18.5), (date(2026, 1, 5), 19.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_factor_entry()]), {"fred": source})

    engine.ingest(full=True)
    stg = db.query_polars("SELECT * FROM stg_market_factors ORDER BY as_of_date")
    fct = db.query_polars("SELECT * FROM fct_market_factors ORDER BY valuation_date")
    assert stg.height == 2
    assert stg["frequency"].to_list() == ["DAILY", "DAILY"]
    assert fct.height == 2
    assert fct["value"].to_list() == pytest.approx([18.5, 19.0])
    db.close()


def test_engine_forward_fills_monthly_factor_to_business_days() -> None:
    db = _db()
    source = FakeSource({"IPC": [(date(2026, 1, 2), 100.0), (date(2026, 2, 2), 101.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_monthly_entry()]), {"bcch": source})

    engine.ingest(full=True)
    stg = db.query_polars("SELECT * FROM stg_market_factors ORDER BY as_of_date")
    fct = db.query_polars("SELECT * FROM fct_market_factors ORDER BY valuation_date")

    assert stg.height == 2
    calendar = SantiagoCalendar()
    expected_days = calendar.business_days_between(date(2026, 1, 2), date(2026, 2, 2))
    assert fct.height == expected_days
    assert set(fct["value"].unique().to_list()) == {100.0, 101.0}
    # Forward-fill is monotonic non-decreasing along the date axis.
    assert fct["value"].to_list() == sorted(fct["value"].to_list())
    db.close()


def test_engine_bootstraps_curve_to_zero_rates() -> None:
    db = _db()
    source = FakeSource(
        {
            "BCP2": [(date(2026, 1, 2), 5.0)],
            "BCP5": [(date(2026, 1, 2), 5.5)],
            "BCP10": [(date(2026, 1, 2), 6.0)],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[_curve_entry()]), {"bcch": source})

    engine.ingest(full=True)
    stg = db.query_polars("SELECT * FROM stg_market_curves ORDER BY tenor_yf")
    fct = db.query_polars("SELECT * FROM fct_market_curves ORDER BY tenor_yf")

    assert stg["par_rate"].to_list() == pytest.approx([0.05, 0.055, 0.06])
    zeros = fct["zero_rate"].to_list()
    assert len(zeros) == 3
    # Upward-sloping par curve -> increasing zero rates.
    assert zeros == sorted(zeros)
    # Closed-form 2Y zero rate for a 5% par bond with annual coupons.
    assert zeros[0] == pytest.approx(0.0500417, rel=1e-4)
    db.close()


def test_engine_stores_raw_curve_without_bootstrap() -> None:
    db = _db()
    entry = _curve_entry().model_copy(update={"bootstrap": False})
    source = FakeSource(
        {
            "BCP2": [(date(2026, 1, 2), 5.0)],
            "BCP5": [(date(2026, 1, 2), 5.5)],
            "BCP10": [(date(2026, 1, 2), 6.0)],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[entry]), {"bcch": source})

    engine.ingest(full=True)
    stg = db.query_polars("SELECT * FROM stg_market_curves")
    fct = db.query_polars("SELECT * FROM fct_market_curves")
    assert stg.height == 3
    assert fct.height == 0
    db.close()


def test_engine_incremental_sync_fetches_only_new_rows() -> None:
    db = _db()
    source = FakeSource({"TCO": [(date(2026, 1, 2), 900.25), (date(2026, 1, 5), 910.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_fx_entry()]), {"bcch": source})
    engine.ingest(full=True)

    source_with_new = FakeSource(
        {
            "TCO": [
                (date(2026, 1, 2), 900.25),
                (date(2026, 1, 5), 910.0),
                (date(2026, 1, 6), 912.0),
            ]
        }
    )
    engine2 = IngestionEngine(db, SeriesCatalog(series=[_fx_entry()]), {"bcch": source_with_new})
    results = engine2.ingest()

    assert results[0].rows == 1
    count = db.query_polars("SELECT COUNT(*) AS n FROM fct_fx_rates")
    assert count["n"][0] == 3
    db.close()


def test_engine_reports_missing_source() -> None:
    db = _db()
    engine = IngestionEngine(db, SeriesCatalog(series=[_fx_entry()]), sources={})
    with pytest.raises(ValueError, match="no source registered"):
        engine.ingest(full=True)
    db.close()


def test_engine_fail_fast_false_continues_after_error() -> None:
    db = _db()
    good = _fx_entry()
    bad = good.model_copy(update={"series_id": "bad", "source": "missing", "code": "X"})
    source = FakeSource({"TCO": [(date(2026, 1, 2), 900.25)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[bad, good]), {"bcch": source})

    results = engine.ingest(full=True, fail_fast=False)

    assert [result.status for result in results] == ["error", "ok"]
    assert db.query_polars("SELECT COUNT(*) AS n FROM fct_fx_rates")["n"][0] == 1
    db.close()


def test_validation_registry_covers_new_tables() -> None:
    # Guard: the engine relies on these contracts existing.
    for table in (
        "fct_market_factors",
        "fct_banking_volumes",
        "stg_market_curves",
        "stg_market_factors",
    ):
        assert table in SCHEMAS


def _volume_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="dep_cc_vol",
        source="bcch",
        adapter="volume",
        target_table="fct_banking_volumes",
        frequency="DAILY",
        code="D1A",
        instrument_code="DEP_CC",
        currency="CLP",
        start_date=date(2026, 1, 1),
    )


def test_engine_ingests_banking_volume() -> None:
    db = _db()
    source = FakeSource({"D1A": [(date(2026, 1, 2), 5000.0), (date(2026, 1, 5), 5100.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[_volume_entry()]), {"bcch": source})

    engine.ingest(full=True)
    frame = db.query_polars("SELECT * FROM fct_banking_volumes ORDER BY as_of_date")
    assert frame.height == 2
    assert frame["instrument_code"].to_list() == ["DEP_CC", "DEP_CC"]
    assert frame["amount"].to_list() == pytest.approx([5000.0, 5100.0])
    assert frame["frequency"].to_list() == ["DAILY", "DAILY"]
    db.close()


def test_engine_forward_fills_quarterly_factor() -> None:
    db = _db()
    entry = _factor_entry().model_copy(update={"frequency": "QUARTERLY", "factor_code": "GDP"})
    source = FakeSource({"VIXCLS": [(date(2026, 1, 2), 100.0), (date(2026, 4, 2), 101.0)]})
    engine = IngestionEngine(db, SeriesCatalog(series=[entry]), {"fred": source})

    engine.ingest(full=True)
    stg = db.query_polars("SELECT * FROM stg_market_factors ORDER BY as_of_date")
    fct = db.query_polars("SELECT * FROM fct_market_factors ORDER BY valuation_date")

    assert stg.height == 2
    assert stg["frequency"].to_list() == ["QUARTERLY", "QUARTERLY"]
    assert fct.height > 2
    assert set(fct["value"].unique().to_list()) == {100.0, 101.0}
    db.close()


def test_engine_forward_fills_missing_tenor_before_bootstrap() -> None:
    db = _db()
    # 2Y is missing on 2026-01-05 but only 1 business day from its last value.
    source = FakeSource(
        {
            "BCP2": [(date(2026, 1, 2), 5.0), (date(2026, 1, 6), 5.0)],
            "BCP5": [
                (date(2026, 1, 2), 5.5),
                (date(2026, 1, 5), 5.5),
                (date(2026, 1, 6), 5.5),
            ],
            "BCP10": [
                (date(2026, 1, 2), 6.0),
                (date(2026, 1, 5), 6.0),
                (date(2026, 1, 6), 6.0),
            ],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[_curve_entry()]), {"bcch": source})

    engine.ingest(full=True)
    fct = db.query_polars(
        "SELECT valuation_date, COUNT(*) AS n FROM fct_market_curves GROUP BY 1 ORDER BY 1"
    )
    assert fct["valuation_date"].to_list() == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    assert fct["n"].to_list() == [3, 3, 3]
    db.close()


def test_engine_drops_date_when_tenor_gap_exceeds_fill_window() -> None:
    db = _db()
    # 2Y is stale by 7 business days on 2026-01-13 (> 5), so that date is dropped.
    source = FakeSource(
        {
            "BCP2": [(date(2026, 1, 2), 5.0), (date(2026, 1, 15), 5.0)],
            "BCP5": [
                (date(2026, 1, 2), 5.5),
                (date(2026, 1, 13), 5.5),
                (date(2026, 1, 15), 5.5),
            ],
            "BCP10": [
                (date(2026, 1, 2), 6.0),
                (date(2026, 1, 13), 6.0),
                (date(2026, 1, 15), 6.0),
            ],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[_curve_entry()]), {"bcch": source})

    engine.ingest(full=True)
    dates = db.query_polars("SELECT DISTINCT valuation_date FROM fct_market_curves ORDER BY 1")[
        "valuation_date"
    ].to_list()
    # 01-13 has 5Y/10Y but its 2Y is 7 business days stale (> 5) -> excluded.
    assert date(2026, 1, 13) not in dates
    assert date(2026, 1, 12) not in dates
    assert date(2026, 1, 2) in dates
    assert date(2026, 1, 15) in dates
    db.close()


def _swap_entry() -> SeriesEntry:
    return SeriesEntry(
        series_id="clp_spc",
        source="bcch",
        adapter="curve",
        target_table="fct_market_curves",
        frequency="DAILY",
        curve_name="CLP_SPC",
        bootstrap=True,
        curve_type="SWAP",
        coupon_frequency="SEMIANNUAL",
        day_count="ACT/360",
        codes={"1Y": "SPC1", "2Y": "SPC2", "3Y": "SPC3"},
        start_date=date(2026, 1, 1),
    )


def test_engine_bootstraps_swap_curve() -> None:
    db = _db()
    source = FakeSource(
        {
            "SPC1": [(date(2026, 1, 2), 5.0)],
            "SPC2": [(date(2026, 1, 2), 5.0)],
            "SPC3": [(date(2026, 1, 2), 5.0)],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[_swap_entry()]), {"bcch": source})

    engine.ingest(full=True)
    fct = db.query_polars("SELECT * FROM fct_market_curves ORDER BY tenor_yf")

    assert fct.height == 3
    assert fct["zero_rate"].is_finite().all()
    assert (fct["zero_rate"] > 0.0).all()
    db.close()


def test_engine_ignores_stale_staging_tenors() -> None:
    db = _db()
    stale = pl.DataFrame(
        {
            "valuation_date": [date(2026, 1, 2)],
            "curve_name": ["CLP_BCP"],
            "tenor_label": ["1Y"],
            "tenor_yf": [1.0],
            "par_rate": [0.04],
            "source": ["BCCh"],
        }
    )
    db.bulk_insert_polars(stale, "stg_market_curves")
    source = FakeSource(
        {
            "BCP2": [(date(2026, 1, 2), 5.0)],
            "BCP5": [(date(2026, 1, 2), 5.5)],
            "BCP10": [(date(2026, 1, 2), 6.0)],
        }
    )
    engine = IngestionEngine(db, SeriesCatalog(series=[_curve_entry()]), {"bcch": source})

    engine.ingest(full=True)
    tenors = db.query_polars("SELECT DISTINCT tenor_label FROM fct_market_curves ORDER BY 1")[
        "tenor_label"
    ].to_list()
    assert tenors == ["10Y", "2Y", "5Y"]
    db.close()
