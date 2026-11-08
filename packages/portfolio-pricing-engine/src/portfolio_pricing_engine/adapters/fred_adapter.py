"""FRED (Federal Reserve Economic Data) market-data adapter."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, ClassVar

import httpx
import polars as pl
from risk_data_core.observability import get_logger, timed

from portfolio_pricing_engine.adapters._schemas import ParRateSchema
from portfolio_pricing_engine.domain.bootstrapping import tenor_to_years


class FredAdapter:
    """Adapter for FRED SOFR and US Treasury par-yield series."""

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    CURVE_SERIES: ClassVar[dict[str, dict[str, str]]] = {
        "USD_TREASURY": {"2Y": "DGS2", "5Y": "DGS5", "10Y": "DGS10"},
        "USD_SOFR": {"1D": "SOFR"},
    }

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("FRED_API_KEY", "")
        self._client = client or httpx.Client(timeout=30.0)
        self._logger = get_logger(__name__)

    def _latest_value(self, series_id: str, valuation_date: date) -> float:
        if not self._api_key:
            msg = "FRED_API_KEY is not set"
            raise RuntimeError(msg)
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": (valuation_date - timedelta(days=10)).isoformat(),
            "observation_end": valuation_date.isoformat(),
            "sort_order": "desc",
            "limit": 1,
        }
        with timed(self._logger, "fred_get_series", series_id=series_id):
            response = self._client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        observations = payload.get("observations", [])
        if not observations:
            msg = f"FRED series {series_id} returned no observations for {valuation_date}"
            raise ValueError(msg)
        value = observations[0]["value"]
        if value == ".":
            msg = f"FRED series {series_id} has a missing value for {valuation_date}"
            raise ValueError(msg)
        return float(value)

    def fetch_par_rates(self, valuation_date: date, curve_name: str) -> pl.DataFrame:
        """Fetch par rates (as decimals) for a FRED curve."""
        try:
            series = self.CURVE_SERIES[curve_name]
        except KeyError:
            msg = f"unknown FRED curve {curve_name!r}"
            raise ValueError(msg) from None
        rows = [
            {
                "tenor_label": tenor,
                "tenor_yf": tenor_to_years(tenor),
                "par_rate": self._latest_value(series_id, valuation_date) / 100.0,
            }
            for tenor, series_id in series.items()
        ]
        frame = pl.DataFrame(rows)
        return ParRateSchema.validate(frame).sort("tenor_yf")

    def _get_observations(self, series_id: str, start: date, end: date) -> list[dict[str, Any]]:
        """Return the raw ``observations`` for a FRED series over ``[start, end]``."""
        if not self._api_key:
            msg = "FRED_API_KEY is not set"
            raise RuntimeError(msg)
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "sort_order": "asc",
        }
        with timed(self._logger, "fred_get_series", series_id=series_id):
            response = self._client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        return payload.get("observations", [])

    def fetch_series_history(self, series_id: str, start: date, end: date) -> pl.DataFrame:
        """Fetch a full FRED series over ``[start, end]`` as ``(as_of_date, value)``.

        Values are returned exactly as published (no scaling). Missing ``"."``
        observations are skipped.
        """
        rows: list[dict[str, Any]] = []
        for obs in self._get_observations(series_id, start, end):
            raw = obs.get("value")
            if raw in (None, ".", ""):
                continue
            rows.append({"as_of_date": date.fromisoformat(obs["date"]), "value": float(raw)})
        schema = {"as_of_date": pl.Date, "value": pl.Float64}
        return pl.DataFrame(rows, schema=schema).sort("as_of_date")

    def fetch_curve_history(
        self,
        codes: dict[str, str],
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """Fetch par-rate history for every tenor of a FRED curve.

        ``codes`` maps tenor label (e.g. ``"10Y"``) to a FRED series id. Rates
        are normalized to decimal (percent / 100) for the bootstrapping layer.
        """
        frames: list[pl.DataFrame] = []
        for tenor, series_id in codes.items():
            history = self.fetch_series_history(series_id, start, end)
            frames.append(
                history.rename({"as_of_date": "valuation_date", "value": "par_rate"}).with_columns(
                    [
                        pl.lit(tenor).alias("tenor_label"),
                        pl.lit(tenor_to_years(tenor)).alias("tenor_yf"),
                    ]
                )
            )
        schema = {
            "valuation_date": pl.Date,
            "tenor_label": pl.String,
            "tenor_yf": pl.Float64,
            "par_rate": pl.Float64,
        }
        if not frames:
            return pl.DataFrame(schema=schema)
        combined = pl.concat(frames)
        return combined.with_columns((pl.col("par_rate") / 100.0).alias("par_rate")).sort(
            ["valuation_date", "tenor_yf"]
        )


__all__ = ["FredAdapter"]
