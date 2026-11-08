"""Adapter for the Chilean government holiday API (digital.gob.cl)."""

from __future__ import annotations

from datetime import date

import httpx
import polars as pl
from risk_data_core.observability import get_logger, timed


class DigitalGobHolidayAdapter:
    """Fetch the official Chilean holiday list for a year.

    The live endpoint is ``https://apis.digital.gob.cl/fl/feriados/{year}``.
    It may be unreachable in restricted environments; callers can inject an
    ``httpx.Client`` (for tests or a proxy).
    """

    BASE_URL = "https://apis.digital.gob.cl/fl/feriados"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._logger = get_logger(__name__)

    def fetch_year(self, year: int) -> pl.DataFrame:
        """Return ``date``, ``name`` and ``kind`` for the official holidays."""
        with timed(self._logger, "digital_gob_holidays", year=year):
            response = self._client.get(f"{self.BASE_URL}/{year}")
            response.raise_for_status()
            payload = response.json()
        rows = [
            {
                "date": date.fromisoformat(str(item["fecha"])),
                "name": str(item.get("nombre", "")),
                "kind": str(item.get("tipo", "")),
            }
            for item in payload
            if item.get("fecha")
        ]
        return pl.DataFrame(
            rows,
            schema={"date": pl.Date, "name": pl.String, "kind": pl.String},
        )


__all__ = ["DigitalGobHolidayAdapter"]
