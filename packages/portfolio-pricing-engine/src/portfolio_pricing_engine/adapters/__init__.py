"""External market-data adapters."""

from portfolio_pricing_engine.adapters.alpaca_adapter import AlpacaAdapter
from portfolio_pricing_engine.adapters.bcch_adapter import BcchAdapter
from portfolio_pricing_engine.adapters.digital_gob_holiday_adapter import (
    DigitalGobHolidayAdapter,
)
from portfolio_pricing_engine.adapters.duckdb_trade_repository import DuckDBTradeRepository
from portfolio_pricing_engine.adapters.fred_adapter import FredAdapter

__all__ = [
    "AlpacaAdapter",
    "BcchAdapter",
    "DigitalGobHolidayAdapter",
    "DuckDBTradeRepository",
    "FredAdapter",
]
