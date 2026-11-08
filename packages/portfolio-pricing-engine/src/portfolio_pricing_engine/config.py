"""Typed catalog for the parametrized market-data ingestion engine.

The catalog is a YAML document validated against these Pydantic models; the
ingestion engine reads it and dispatches each entry to the matching adapter.
The YAML file is data, not code: adding a series never requires a code change.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["bcch", "fred"]
AdapterKind = Literal["fx", "factor", "curve", "volume"]
# Final analytical destination table per entry. Staging tables are derived by
# the engine (``stg_market_curves`` / ``stg_market_factors``).
TargetTable = Literal[
    "fct_fx_rates",
    "fct_market_factors",
    "fct_market_curves",
    "fct_banking_volumes",
]
Frequency = Literal["DAILY", "MONTHLY", "QUARTERLY"]


class SeriesEntry(BaseModel):
    """One parametrized market-data series."""

    model_config = ConfigDict(frozen=True)

    series_id: str
    source: Source
    adapter: AdapterKind
    target_table: TargetTable
    frequency: Frequency
    start_date: date
    description: str = ""

    # fx / factor: single source-series code.
    code: str | None = None
    # factor: output key persisted in ``fct_market_factors``.
    factor_code: str | None = None
    # fx: currency pair persisted in ``fct_fx_rates``.
    currency_pair: str | None = None
    # volume: instrument code persisted in ``fct_banking_volumes``.
    instrument_code: str | None = None
    # volume: balance currency (e.g. ``CLP``, ``UF``, ``USD``).
    currency: str | None = None
    # curve: tenor label -> source-series code mapping.
    codes: dict[str, str] | None = None
    # curve: analytical curve name persisted in the curves tables.
    curve_name: str | None = None
    # curve: bootstrap par-yield rates into zero rates (``fct_market_curves``).
    # ``False`` stores raw rates in ``stg_market_curves`` only (e.g. swap/OIS
    # curves whose zero construction is a distinct algorithm).
    bootstrap: bool = False
    # curve: risk regime for the no-arbitrage screen (REAL for UF curves, which
    # can carry negative real rates and discount factors above 1.0).
    curve_regime: Literal["NOMINAL", "REAL"] = "NOMINAL"
    # curve: zero-construction convention (BOND par yields vs SWAP hybrid curve).
    curve_type: Literal["BOND", "SWAP"] = "BOND"
    coupon_frequency: Literal["SEMIANNUAL", "ANNUAL"] = "SEMIANNUAL"
    day_count: Literal["ACT/360", "ACT/365", "30/360"] = "ACT/360"
    # curve: optional no-arbitrage forward floors (decimals); None -> regime default.
    min_forward_warn: float | None = None
    min_forward: float | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> SeriesEntry:
        if self.adapter == "fx":
            self._require(self.code, "fx entries require 'code'")
            self._require(self.currency_pair, "fx entries require 'currency_pair'")
            if self.target_table != "fct_fx_rates":
                msg = "fx entries must target 'fct_fx_rates'"
                raise ValueError(msg)
        elif self.adapter == "factor":
            self._require(self.code, "factor entries require 'code'")
            self._require(self.factor_code, "factor entries require 'factor_code'")
            if self.target_table != "fct_market_factors":
                msg = "factor entries must target 'fct_market_factors'"
                raise ValueError(msg)
        elif self.adapter == "curve":
            self._require(self.codes, "curve entries require 'codes'")
            self._require(self.curve_name, "curve entries require 'curve_name'")
            if self.target_table != "fct_market_curves":
                msg = "curve entries must target 'fct_market_curves'"
                raise ValueError(msg)
        elif self.adapter == "volume":
            self._require(self.code, "volume entries require 'code'")
            self._require(self.instrument_code, "volume entries require 'instrument_code'")
            self._require(self.currency, "volume entries require 'currency'")
            if self.target_table != "fct_banking_volumes":
                msg = "volume entries must target 'fct_banking_volumes'"
                raise ValueError(msg)
        return self

    @staticmethod
    def _require(value: object | None, message: str) -> None:
        if value is None or value == "":
            raise ValueError(message)


class SeriesCatalog(BaseModel):
    """Top-level validated catalog of market-data series."""

    model_config = ConfigDict(frozen=True)

    series: list[SeriesEntry] = Field(default_factory=list)

    def by_id(self) -> dict[str, SeriesEntry]:
        return {entry.series_id: entry for entry in self.series}

    def select(self, series_ids: set[str] | None = None) -> list[SeriesEntry]:
        """Return all entries, optionally filtered by ``series_ids``."""
        if series_ids is None:
            return list(self.series)
        return [entry for entry in self.series if entry.series_id in series_ids]


def load_catalog(path: str | Path) -> SeriesCatalog:
    """Load and validate a series catalog from a YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "series" not in raw:
        msg = f"catalog {path!s} must contain a top-level 'series' list"
        raise ValueError(msg)
    return SeriesCatalog.model_validate(raw)


__all__ = [
    "AdapterKind",
    "Frequency",
    "SeriesCatalog",
    "SeriesEntry",
    "Source",
    "TargetTable",
    "load_catalog",
]
