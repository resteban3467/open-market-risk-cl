"""Alpaca market-data adapter (paper trading / daily OHLCV)."""

from __future__ import annotations

import os
from datetime import date

import httpx
import polars as pl
from risk_data_core.observability import get_logger, timed

from portfolio_pricing_engine.adapters._schemas import EquityPriceSchema


class AlpacaAdapter:
    """Adapter for Alpaca's daily bar endpoint."""

    BASE_URL = "https://data.alpaca.markets/v2/stocks/bars"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        headers = (
            {
                "APCA-API-KEY-ID": self._api_key,
                "APCA-API-SECRET-KEY": self._api_secret,
            }
            if self._api_key and self._api_secret
            else {}
        )
        self._client = client or httpx.Client(timeout=30.0, headers=headers)
        self._logger = get_logger(__name__)

    def fetch_price(self, valuation_date: date, symbol: str) -> pl.DataFrame:
        """Fetch the latest daily close for ``symbol`` and validate it."""
        if not (self._api_key and self._api_secret):
            msg = "ALPACA_API_KEY / ALPACA_API_SECRET are not set"
            raise RuntimeError(msg)
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": valuation_date.isoformat(),
            "end": valuation_date.isoformat(),
            "limit": 1,
            "feed": "iex",
        }
        with timed(self._logger, "alpaca_get_bars", symbol=symbol):
            response = self._client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        bars = payload.get("bars", {}).get(symbol, [])
        if not bars:
            msg = f"Alpaca returned no bars for {symbol} on {valuation_date}"
            raise ValueError(msg)
        frame = pl.DataFrame(
            {
                "symbol": [symbol],
                "price": [float(bars[-1]["c"])],
                "currency": ["USD"],
            }
        )
        return EquityPriceSchema.validate(frame)


__all__ = ["AlpacaAdapter"]
