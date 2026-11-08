"""Banco Central de Chile (BDE / Siete) market-data adapter."""

from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

import httpx
import polars as pl
from risk_data_core.observability import get_logger, timed
from risk_data_core.validation import validate_ingestion

from portfolio_pricing_engine.adapters._schemas import ParRateSchema
from portfolio_pricing_engine.domain.bootstrapping import tenor_to_years


def _decode_json(response: httpx.Response) -> dict[str, Any]:
    """Decode a BCCh JSON body (served with an ISO-8859-1 charset)."""
    try:
        return response.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json.loads(response.content.decode("iso-8859-1", errors="replace"))


class BcchAdapter:
    """Adapter for BCCh BDE series (BCP, BCU, TPM, UF and USD/CLP)."""

    BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"

    CURVE_SERIES: ClassVar[dict[str, dict[str, str]]] = {
        "CLP_BCP": {
            "2Y": "F022.BCLP.TIS.AN02.NO.Z.D",
            "5Y": "F022.BCLP.TIS.AN05.NO.Z.D",
            "10Y": "F022.BCLP.TIS.AN10.NO.Z.D",
        },
        "CLP_BCU": {
            "1Y": "F022.BUF.TIS.AN01.UF.Z.D",
            "2Y": "F022.BUF.TIS.AN02.UF.Z.D",
            "5Y": "F022.BUF.TIS.AN05.UF.Z.D",
            "10Y": "F022.BUF.TIS.AN10.UF.Z.D",
        },
        "CLP_TPM": {"1D": "F022.TPM.TIN.D001.NO.Z.D"},
    }
    FX_SERIES: ClassVar[dict[str, str]] = {"USD/CLP": "F073.TCO.PRE.Z.D"}

    def __init__(
        self,
        token: str | None = None,
        client: httpx.Client | None = None,
        *,
        min_interval: float = 0.25,
        max_attempts: int = 3,
    ) -> None:
        self._token = token or os.environ.get("BCCH_API_TOKEN", "")
        self._client = client or httpx.Client(timeout=30.0)
        self._logger = get_logger(__name__)
        # BCCh allows up to 5 series/second per account: 0.25s -> 4 req/s.
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._last_request = 0.0
        self._rate_lock = threading.Lock()

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(8.0, 2.0 ** (attempt - 1)) + random.random())

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET the BDE REST endpoint with throttling and retry/backoff."""
        if not self._token:
            msg = "BCCH_API_TOKEN is not set"
            raise RuntimeError(msg)
        full = {"token": self._token, **params}
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                with timed(
                    self._logger,
                    "bcch_request",
                    function=params.get("function"),
                    series_code=params.get("timeseries"),
                ):
                    response = self._client.get(self.BASE_URL, params=full)
                    response.raise_for_status()
                    payload = _decode_json(response)
                codigo = payload.get("Codigo")
                if codigo not in (0, None):
                    msg = f"BCCh API error {codigo}: {payload.get('Descripcion')}"
                    raise ValueError(msg)
                return payload
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504):
                    raise
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
            if attempt < self._max_attempts:
                self._backoff(attempt)
        assert last_exc is not None
        raise last_exc

    def _latest_value(self, series_code: str, valuation_date: date) -> float:
        payload = self._request(
            {
                "function": "GetSeries",
                "timeseries": series_code,
                "firstdate": (valuation_date - timedelta(days=10)).isoformat(),
                "lastdate": valuation_date.isoformat(),
            }
        )
        observations = (payload.get("Series") or {}).get("Obs") or []
        for obs in reversed(observations):
            if obs.get("statusCode", "OK") != "OK":
                continue
            raw = obs.get("value")
            if raw in (None, "", "NA", "NaN"):
                continue
            return float(raw)
        msg = f"BCCh series {series_code} returned no observations for {valuation_date}"
        raise ValueError(msg)

    def fetch_par_rates(self, valuation_date: date, curve_name: str) -> pl.DataFrame:
        """Fetch par rates (as decimals) for a BCCh curve."""
        try:
            series = self.CURVE_SERIES[curve_name]
        except KeyError:
            msg = f"unknown BCCh curve {curve_name!r}"
            raise ValueError(msg) from None
        rows = [
            {
                "tenor_label": tenor,
                "tenor_yf": tenor_to_years(tenor),
                "par_rate": self._latest_value(code, valuation_date) / 100.0,
            }
            for tenor, code in series.items()
        ]
        frame = pl.DataFrame(rows)
        return ParRateSchema.validate(frame).sort("tenor_yf")

    def fetch_fx(self, valuation_date: date, currency_pair: str) -> pl.DataFrame:
        """Fetch an exchange rate and validate it against ``fct_fx_rates``."""
        try:
            code = self.FX_SERIES[currency_pair]
        except KeyError:
            msg = f"unknown BCCh FX pair {currency_pair!r}"
            raise ValueError(msg) from None
        frame = pl.DataFrame(
            {
                "valuation_date": [valuation_date],
                "currency_pair": [currency_pair],
                "spot_rate": [self._latest_value(code, valuation_date)],
                "source": ["BCCh"],
            }
        )
        return validate_ingestion(frame, "fct_fx_rates")

    def _get_observations(self, code: str, start: date, end: date) -> list[dict[str, Any]]:
        """Return the raw ``Obs`` list for a BCCh series over ``[start, end]``."""
        payload = self._request(
            {
                "function": "GetSeries",
                "timeseries": code,
                "firstdate": start.isoformat(),
                "lastdate": end.isoformat(),
            }
        )
        return (payload.get("Series") or {}).get("Obs") or []

    def search_series(self, frequency: str) -> pl.DataFrame:
        """Return the BDE catalog for a frequency (DAILY/MONTHLY/QUARTERLY/ANNUAL).

        Useful to resolve or verify series codes without hand-maintaining them.
        """
        payload = self._request({"function": "SearchSeries", "frequency": frequency})
        infos = payload.get("SeriesInfos") or []
        schema = {
            "series_id": pl.String,
            "frequency": pl.String,
            "spanish_title": pl.String,
            "english_title": pl.String,
            "first_observation": pl.String,
            "last_observation": pl.String,
        }
        rows = [
            {
                "series_id": info.get("seriesId"),
                "frequency": info.get("frequencyCode"),
                "spanish_title": info.get("spanishTitle"),
                "english_title": info.get("englishTitle"),
                "first_observation": info.get("firstObservation"),
                "last_observation": info.get("lastObservation"),
            }
            for info in infos
        ]
        return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)

    def fetch_series_history(self, code: str, start: date, end: date) -> pl.DataFrame:
        """Fetch a full BCCh series over ``[start, end]`` as ``(as_of_date, value)``.

        Values are returned exactly as published by the source (no scaling).
        Non-``OK`` observations (holidays / missing data) are skipped.
        """
        rows: list[dict[str, Any]] = []
        for obs in self._get_observations(code, start, end):
            if obs.get("statusCode") != "OK":
                continue
            raw = obs.get("value")
            if raw in (None, "", "NA", "NaN"):
                continue
            rows.append(
                {
                    "as_of_date": datetime.strptime(obs["indexDateString"], "%d-%m-%Y").date(),
                    "value": float(raw),
                }
            )
        schema = {"as_of_date": pl.Date, "value": pl.Float64}
        return pl.DataFrame(rows, schema=schema).sort("as_of_date")

    def fetch_curve_history(
        self,
        codes: dict[str, str],
        start: date,
        end: date,
    ) -> pl.DataFrame:
        """Fetch par-rate history for every tenor of a curve.

        ``codes`` maps tenor label (e.g. ``"2Y"``) to a BCCh series code. Rates
        are normalized to decimal (percent / 100) for the bootstrapping layer.
        """
        frames: list[pl.DataFrame] = []
        for tenor, code in codes.items():
            history = self.fetch_series_history(code, start, end)
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


__all__ = ["BcchAdapter"]
