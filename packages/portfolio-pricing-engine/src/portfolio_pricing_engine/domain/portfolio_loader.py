"""End-of-day portfolio pipeline orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import polars as pl
from risk_data_core import RiskDatabase
from risk_data_core.observability import get_logger, timed
from risk_data_core.validation import validate_ingestion

from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve
from portfolio_pricing_engine.domain.cashflow_engine import compute_pv_dv01, expand_cashflows
from portfolio_pricing_engine.ports.market_data_ports import YieldCurvePort
from portfolio_pricing_engine.ports.portfolio_ports import TradeRepositoryPort


class PortfolioLoader:
    """Run the EOD pipeline: load -> bootstrap -> expand -> value -> persist."""

    def __init__(
        self,
        db: RiskDatabase,
        repository: TradeRepositoryPort,
        curve_provider: YieldCurvePort | None = None,
    ) -> None:
        self._db = db
        self._repository = repository
        self._curve_provider = curve_provider
        self._logger = get_logger(__name__)

    def run_eod(
        self,
        valuation_date: date,
        curve_name: str = "CLP_BCP",
        *,
        curve: ZeroCurve | None = None,
        conversion_factors: Mapping[str, float] | None = None,
    ) -> pl.DataFrame:
        """Price the active portfolio and upsert ``fct_valued_cashflows``.

        ``curve`` may be injected (e.g. read from ``fct_market_curves``); when
        omitted it is bootstrapped from ``curve_provider``. ``conversion_factors``
        maps leg currency -> CLP factor (UF value, USD/CLP, 1.0 for CLP).
        """
        with timed(
            self._logger,
            "run_eod",
            valuation_date=valuation_date,
            curve_name=curve_name,
        ):
            trades = self._repository.load_trades(valuation_date)
            legs = self._repository.load_legs(valuation_date)
            if curve is None:
                if self._curve_provider is None:
                    msg = "run_eod requires either an injected curve or a curve_provider"
                    raise ValueError(msg)
                par_rates = self._curve_provider.fetch_par_rates(valuation_date, curve_name)
                curve = ZeroCurve.from_par_rates(par_rates, valuation_date)
            cashflows = expand_cashflows(trades, legs, valuation_date)
            valued = compute_pv_dv01(cashflows, curve, conversion_factors=conversion_factors)
            validated = validate_ingestion(valued, "fct_valued_cashflows")
            self._db.bulk_insert_polars(validated, "fct_valued_cashflows")
            return validated


__all__ = ["PortfolioLoader"]
