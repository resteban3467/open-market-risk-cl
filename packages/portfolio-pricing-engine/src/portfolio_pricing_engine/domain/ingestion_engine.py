"""Parametrized bulk market-data ingestion engine.

Reads a :class:`~portfolio_pricing_engine.config.SeriesCatalog`, fetches each
series through a :class:`~portfolio_pricing_engine.ports.market_data_ports.SeriesHistoryPort`,
and persists it idempotently through the shared kernel.

Routing per catalog entry:

- ``fx``     -> ``fct_fx_rates``.
- ``factor`` -> ``stg_market_factors`` (native frequency) then forward-filled
  into ``fct_market_factors`` (daily grain).
- ``curve``  -> ``stg_market_curves`` (raw par rates) then, when
  ``bootstrap=True``, bootstrapped zero rates into ``fct_market_curves``.
- ``volume``  -> ``fct_banking_volumes`` (deposit / loan balances, native freq).

Only ``risk-data-core`` is imported (clean-architecture boundary).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast

import polars as pl
from risk_data_core import RiskDatabase, SantiagoCalendar
from risk_data_core.observability import get_logger, timed
from risk_data_core.validation import validate_curve_no_arbitrage, validate_ingestion

from portfolio_pricing_engine.config import SeriesCatalog, SeriesEntry
from portfolio_pricing_engine.domain.bootstrapping import (
    bootstrap_swap_curve,
    bootstrap_zero_rates,
)
from portfolio_pricing_engine.ports.market_data_ports import SeriesHistoryPort

_SOURCE_LABELS = {"bcch": "BCCh", "fred": "FRED"}


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting a single catalog entry."""

    series_id: str
    adapter: str
    source: str
    rows: int
    status: str
    start: date
    end: date


class IngestionEngine:
    """Orchestrates bulk ingestion of the series catalog into DuckDB."""

    def __init__(
        self,
        db: RiskDatabase,
        catalog: SeriesCatalog,
        sources: Mapping[str, SeriesHistoryPort],
        calendar: SantiagoCalendar | None = None,
    ) -> None:
        self._db = db
        self._catalog = catalog
        self._sources = sources
        self._calendar = calendar or SantiagoCalendar()
        self._logger = get_logger(__name__)

    # ------------------------------------------------------------------ public
    def ingest(
        self,
        series_ids: set[str] | None = None,
        start: date | None = None,
        end: date | None = None,
        *,
        full: bool = False,
        fail_fast: bool = True,
    ) -> list[IngestResult]:
        """Ingest every selected series and return one result per entry.

        When ``fail_fast`` is ``False`` a failing series is logged and recorded
        with ``status="error"`` instead of aborting the whole batch.
        """
        end = end or date.today()
        results: list[IngestResult] = []
        for entry in self._catalog.select(series_ids):
            try:
                with timed(
                    self._logger,
                    "ingest_series",
                    series_id=entry.series_id,
                    adapter=entry.adapter,
                ):
                    results.append(self._ingest_entry(entry, start, end, full))
            except Exception as exc:
                if fail_fast:
                    raise
                self._logger.error(
                    "ingest_error",
                    series_id=entry.series_id,
                    adapter=entry.adapter,
                    error=str(exc),
                )
                results.append(
                    IngestResult(
                        entry.series_id,
                        entry.adapter,
                        entry.source,
                        0,
                        "error",
                        start or entry.start_date,
                        end,
                    )
                )
        return results

    # ------------------------------------------------------------- per-entry
    def _ingest_entry(
        self,
        entry: SeriesEntry,
        start: date | None,
        end: date,
        full: bool,
    ) -> IngestResult:
        effective_start = self._effective_start(entry, start, full)
        if effective_start > end:
            return IngestResult(
                entry.series_id,
                entry.adapter,
                entry.source,
                0,
                "up_to_date",
                effective_start,
                end,
            )

        if entry.adapter == "fx":
            rows = self._ingest_fx(entry, effective_start, end)
        elif entry.adapter == "factor":
            rows = self._ingest_factor(entry, effective_start, end)
        elif entry.adapter == "volume":
            rows = self._ingest_volume(entry, effective_start, end)
        else:
            rows = self._ingest_curve(entry, effective_start, end)

        status = "empty" if rows == 0 else "ok"
        return IngestResult(
            entry.series_id,
            entry.adapter,
            entry.source,
            rows,
            status,
            effective_start,
            end,
        )

    def _effective_start(self, entry: SeriesEntry, start: date | None, full: bool) -> date:
        if start is not None:
            return start
        if full:
            return entry.start_date
        last = self._last_ingested(entry)
        if last is None:
            return entry.start_date
        return last + timedelta(days=1)

    def _last_ingested(self, entry: SeriesEntry) -> date | None:
        if entry.adapter == "fx":
            table, date_col, key_col, key_val = (
                "fct_fx_rates",
                "valuation_date",
                "currency_pair",
                entry.currency_pair,
            )
        elif entry.adapter == "factor":
            table, date_col, key_col, key_val = (
                "stg_market_factors",
                "as_of_date",
                "factor_code",
                entry.factor_code,
            )
        elif entry.adapter == "volume":
            table, date_col, key_col, key_val = (
                "fct_banking_volumes",
                "as_of_date",
                "instrument_code",
                entry.instrument_code,
            )
        else:
            table, date_col, key_col, key_val = (
                "stg_market_curves",
                "valuation_date",
                "curve_name",
                entry.curve_name,
            )
        assert key_val is not None  # guaranteed by the catalog validator
        result = self._db.query_polars(
            f"SELECT MAX({date_col}) AS last_date FROM {table} WHERE {key_col} = ?",
            [key_val],
        )
        return result["last_date"][0]

    # ------------------------------------------------------------------- fx
    def _ingest_fx(self, entry: SeriesEntry, start: date, end: date) -> int:
        assert entry.code is not None
        assert entry.currency_pair is not None
        history = self._source(entry).fetch_series_history(entry.code, start, end)
        if history.is_empty():
            return 0
        frame = history.rename({"as_of_date": "valuation_date", "value": "spot_rate"}).with_columns(
            [
                pl.lit(entry.currency_pair).alias("currency_pair"),
                pl.lit(_SOURCE_LABELS[entry.source]).alias("source"),
            ]
        )
        validated = validate_ingestion(frame, "fct_fx_rates")
        self._db.bulk_insert_polars(validated, "fct_fx_rates")
        return validated.height

    # --------------------------------------------------------------- factor
    def _ingest_factor(self, entry: SeriesEntry, start: date, end: date) -> int:
        assert entry.code is not None
        assert entry.factor_code is not None
        history = self._source(entry).fetch_series_history(entry.code, start, end)
        if history.is_empty():
            return 0
        native = history.with_columns(
            [
                pl.lit(entry.factor_code).alias("factor_code"),
                pl.lit(_SOURCE_LABELS[entry.source]).alias("source"),
                pl.lit(entry.frequency).alias("frequency"),
            ]
        )
        validated_native = validate_ingestion(native, "stg_market_factors")
        self._db.bulk_insert_polars(validated_native, "stg_market_factors")

        daily = self._to_daily(validated_native)
        validated_daily = validate_ingestion(daily, "fct_market_factors")
        self._db.bulk_insert_polars(validated_daily, "fct_market_factors")
        return validated_native.height

    def _to_daily(self, native: pl.DataFrame) -> pl.DataFrame:
        """Convert native-frequency observations to daily grain (forward-fill)."""
        daily = native.rename({"as_of_date": "valuation_date"}).drop("frequency")
        if native["frequency"][0] == "DAILY":
            return daily
        return self._forward_fill(daily)

    def _forward_fill(self, monthly: pl.DataFrame) -> pl.DataFrame:
        """Forward-fill a single monthly factor onto Santiago business days."""
        if monthly.is_empty():
            return monthly
        obs = monthly.sort("valuation_date").rename({"valuation_date": "obs_date"})
        start = obs["obs_date"][0]
        end = obs["obs_date"][-1]
        bdays = self._business_days(start, end)
        filled = bdays.join_asof(
            obs,
            left_on="valuation_date",
            right_on="obs_date",
            strategy="backward",
        )
        return (
            filled.drop_nulls("value")
            .drop("obs_date")
            .select(["valuation_date", "factor_code", "value", "source"])
        )

    def _business_days(self, start: date, end: date) -> pl.DataFrame:
        years = range(start.year, end.year + 1)
        days = pl.concat([self._calendar.business_days(year) for year in years])
        return days.filter(pl.col("date").is_between(start, end)).rename({"date": "valuation_date"})

    # --------------------------------------------------------------- volume
    def _ingest_volume(self, entry: SeriesEntry, start: date, end: date) -> int:
        assert entry.code is not None
        assert entry.instrument_code is not None
        assert entry.currency is not None
        history = self._source(entry).fetch_series_history(entry.code, start, end)
        if history.is_empty():
            return 0
        frame = history.rename({"value": "amount"}).with_columns(
            [
                pl.lit(entry.instrument_code).alias("instrument_code"),
                pl.lit(entry.currency).alias("currency"),
                pl.lit(_SOURCE_LABELS[entry.source]).alias("source"),
                pl.lit(entry.frequency).alias("frequency"),
            ]
        )
        validated = validate_ingestion(frame, "fct_banking_volumes")
        self._db.bulk_insert_polars(validated, "fct_banking_volumes")
        return validated.height

    # ---------------------------------------------------------------- curve
    def _ingest_curve(self, entry: SeriesEntry, start: date, end: date) -> int:
        assert entry.codes is not None
        assert entry.curve_name is not None
        history = self._source(entry).fetch_curve_history(entry.codes, start, end)
        if history.is_empty():
            return 0
        par = history.with_columns(
            [
                pl.lit(entry.curve_name).alias("curve_name"),
                pl.lit(_SOURCE_LABELS[entry.source]).alias("source"),
            ]
        ).select(["valuation_date", "curve_name", "tenor_label", "tenor_yf", "par_rate", "source"])
        validated_par = validate_ingestion(par, "stg_market_curves")
        self._db.bulk_insert_polars(validated_par, "stg_market_curves")

        if entry.bootstrap:
            lookback_start = self._calendar.add_business_days(start, -10)
            zeros = self._bootstrap_from_stg(entry, start, end)
            if not zeros.is_empty():
                result = validate_curve_no_arbitrage(
                    zeros,
                    regime=entry.curve_regime,
                    warn_forward=entry.min_forward_warn,
                    min_forward=entry.min_forward,
                )
                if not result.clean.is_empty():
                    # Replace the processed window so stale publications that are
                    # no longer complete do not survive the upsert.
                    self._db.execute(
                        "DELETE FROM fct_market_curves WHERE curve_name = ? "
                        "AND valuation_date BETWEEN ? AND ?",
                        [entry.curve_name, lookback_start, end],
                    )
                    validated_zero = validate_ingestion(result.clean, "fct_market_curves")
                    self._db.bulk_insert_polars(validated_zero, "fct_market_curves")
        return validated_par.height

    def _bootstrap_from_stg(
        self,
        entry: SeriesEntry,
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """Read a par window from staging, forward-fill tenors and bootstrap."""
        assert entry.curve_name is not None
        lookback_start = self._calendar.add_business_days(start, -10)
        window = self._db.query_polars(
            "SELECT valuation_date, curve_name, tenor_label, tenor_yf, par_rate, source "
            "FROM stg_market_curves WHERE curve_name = ? AND valuation_date BETWEEN ? AND ?",
            [entry.curve_name, lookback_start, end],
        )
        if entry.codes:
            # Only bootstrap the tenors the catalog currently defines; ignore
            # stale staging rows from previous curve configurations.
            window = window.filter(pl.col("tenor_label").is_in(list(entry.codes)))
        filled = self._forward_fill_tenors(window)
        if filled.is_empty():
            return pl.DataFrame(
                schema={
                    "valuation_date": pl.Date,
                    "curve_name": pl.String,
                    "tenor_label": pl.String,
                    "tenor_yf": pl.Float64,
                    "zero_rate": pl.Float64,
                    "source": pl.String,
                }
            )
        return self._bootstrap_curves(filled, entry)

    def _forward_fill_tenors(
        self,
        par: pl.DataFrame,
        max_business_days: int = 5,
    ) -> pl.DataFrame:
        """Forward-fill missing tenors per business day (bounded lookback).

        Builds a complete ``business_day x tenor`` grid and carries each tenor's
        last observation forward for at most ``max_business_days``. Only dates
        where every tenor has a usable value are returned (no invented curves).
        """
        if par.is_empty():
            return par
        tenors = par.select(["tenor_label", "tenor_yf"]).unique().sort("tenor_yf")
        start = cast(date, par["valuation_date"].min())
        end = cast(date, par["valuation_date"].max())
        bdays = self._business_days(start, end).with_row_index("bd_idx")

        frames: list[pl.DataFrame] = []
        for tenor in tenors["tenor_label"].to_list():
            obs = par.filter(pl.col("tenor_label") == tenor).select(["valuation_date", "par_rate"])
            matched = bdays.join(obs, on="valuation_date", how="left").sort("bd_idx")
            matched = matched.with_columns(
                pl.col("par_rate").forward_fill().alias("_filled"),
                pl.when(pl.col("par_rate").is_not_null())
                .then(pl.col("bd_idx"))
                .otherwise(None)
                .forward_fill()
                .alias("_obs_idx"),
            )
            matched = matched.with_columns(
                pl.when(
                    pl.col("_obs_idx").is_not_null()
                    & ((pl.col("bd_idx") - pl.col("_obs_idx")) <= max_business_days)
                )
                .then(pl.col("_filled"))
                .otherwise(None)
                .alias("par_rate")
            ).select(["valuation_date", pl.lit(tenor).alias("tenor_label"), "par_rate"])
            frames.append(matched)

        filled = pl.concat(frames).join(tenors, on="tenor_label", how="left")
        complete = (
            filled.group_by("valuation_date")
            .agg(pl.col("par_rate").is_not_null().all().alias("_complete"))
            .filter(pl.col("_complete"))
            .select("valuation_date")
        )
        out = filled.join(complete, on="valuation_date", how="inner").drop_nulls("par_rate")
        return out.with_columns(
            pl.lit(par["curve_name"][0]).alias("curve_name"),
            pl.lit(par["source"][0]).alias("source"),
        ).select(["valuation_date", "curve_name", "tenor_label", "tenor_yf", "par_rate", "source"])

    def _bootstrap_curves(self, par: pl.DataFrame, entry: SeriesEntry) -> pl.DataFrame:
        """Bootstrap zero rates per valuation date from a single curve's par rates."""
        frames: list[pl.DataFrame] = []
        for (valuation_date,), group in par.group_by("valuation_date"):
            ordered = group.sort("tenor_yf")
            if entry.curve_type == "SWAP":
                zeros = bootstrap_swap_curve(
                    ordered,
                    coupon_frequency=entry.coupon_frequency,
                    day_count=entry.day_count,
                )
            else:
                zeros = bootstrap_zero_rates(ordered)
            curve_name = ordered["curve_name"][0]
            source = ordered["source"][0]
            frames.append(
                pl.DataFrame(
                    {
                        "valuation_date": [valuation_date] * ordered.height,
                        "curve_name": [curve_name] * ordered.height,
                        "tenor_label": ordered["tenor_label"].to_list(),
                        "tenor_yf": zeros["tenor_yf"].to_list(),
                        "zero_rate": zeros["zero_rate"].to_list(),
                        "source": [source] * ordered.height,
                    }
                )
            )
        schema = {
            "valuation_date": pl.Date,
            "curve_name": pl.String,
            "tenor_label": pl.String,
            "tenor_yf": pl.Float64,
            "zero_rate": pl.Float64,
            "source": pl.String,
        }
        result = pl.concat(frames) if frames else pl.DataFrame(schema=schema)
        if result.is_empty():
            return result
        # Drop any date whose curve produced a non-finite zero rate (bad data).
        bad_dates = (
            result.filter(~pl.col("zero_rate").is_finite()).select("valuation_date").unique()
        )
        return result.join(bad_dates, on="valuation_date", how="anti")

    # --------------------------------------------------------------- helpers
    def _source(self, entry: SeriesEntry) -> SeriesHistoryPort:
        try:
            return self._sources[entry.source]
        except KeyError:
            msg = f"no source registered for {entry.source!r}"
            raise ValueError(msg) from None


__all__ = ["IngestResult", "IngestionEngine"]
