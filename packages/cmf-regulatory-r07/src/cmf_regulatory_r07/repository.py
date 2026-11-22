"""Acceso de solo lectura a los datos necesarios para el calculo del R07.

``cmf-regulatory-r07`` jamas abre la base en modo escritura: el unico paquete
autorizado a hacerlo es ``portfolio-pricing-engine``. Este repositorio exige un
:class:`~risk_data_core.database.RiskDatabase` construido con ``read_only=True``
y consulta exclusivamente mediante ``query_polars`` (Arrow zero-copy).

Las consultas SQL se mantienen simples (``SELECT *``); todo el tratamiento de
datos (filtros de fecha, "ultima observacion", filtro de libros) se hace en
Polars para evitar SQL complejo.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl
from risk_data_core import RiskDatabase
from risk_data_core.observability import get_logger, timed


class RegulatoryDataRepository:
    """Lee ``fct_valued_cashflows``, ``dim_*``, ``fct_fx_rates`` y factores."""

    def __init__(self, db: RiskDatabase) -> None:
        if not db.read_only:
            raise ValueError(
                "RegulatoryDataRepository requires RiskDatabase(read_only=True); "
                "only portfolio-pricing-engine may open the database read-write."
            )
        self._db = db
        self._logger = get_logger(__name__)

    @classmethod
    def from_path(cls, db_path: Path) -> RegulatoryDataRepository:
        """Abre la base en modo ``read_only`` y devuelve el repositorio."""
        return cls(RiskDatabase(Path(db_path), read_only=True))

    @property
    def db(self) -> RiskDatabase:
        return self._db

    def close(self) -> None:
        self._db.close()

    def load_cashflows(self, valuation_date: date) -> pl.DataFrame:
        """Flujos valorizados en la ultima valorizacion ``<=`` la fecha por pata."""
        with timed(self._logger, "load_cashflows", valuation_date=valuation_date.isoformat()):
            frame = self._db.query_polars("SELECT * FROM fct_valued_cashflows")
            if frame.is_empty():
                return frame
            filtered = frame.filter(pl.col("valuation_date") <= valuation_date)
            if filtered.is_empty():
                return filtered
            latest = filtered.group_by(["trade_id", "leg_id"]).agg(
                pl.col("valuation_date").max().alias("_max_valuation_date")
            )
            return (
                filtered.join(latest, on=["trade_id", "leg_id"], how="inner")
                .filter(pl.col("valuation_date") == pl.col("_max_valuation_date"))
                .drop("_max_valuation_date")
            )

    def load_trades(self, books: Sequence[str] | None = None) -> pl.DataFrame:
        """Trades del portafolio; si ``books`` se indica, filtra por libro."""
        with timed(self._logger, "load_trades", books=tuple(books) if books else None):
            frame = self._db.query_polars("SELECT * FROM dim_trades")
            if books is not None:
                frame = frame.filter(pl.col("book").is_in(list(books)))
            return frame

    def load_legs(self) -> pl.DataFrame:
        with timed(self._logger, "load_legs"):
            return self._db.query_polars("SELECT * FROM dim_legs")

    def load_fx_rates(self, valuation_date: date) -> pl.DataFrame:
        """Ultimo tipo de cambio BCCh ``<=`` la fecha para cada par."""
        with timed(self._logger, "load_fx_rates", valuation_date=valuation_date.isoformat()):
            frame = self._db.query_polars("SELECT * FROM fct_fx_rates")
            if frame.is_empty():
                return frame.select(["currency_pair", "spot_rate"])
            return (
                frame.filter(pl.col("valuation_date") <= valuation_date)
                .sort("valuation_date", descending=True)
                .unique(subset=["currency_pair"], keep="first")
                .select(["currency_pair", "spot_rate"])
            )

    def load_market_factors(self, valuation_date: date, codes: Sequence[str]) -> pl.DataFrame:
        """Ultimo valor ``<=`` la fecha de cada ``factor_code`` solicitado."""
        if not codes:
            return pl.DataFrame(schema={"factor_code": pl.String, "value": pl.Float64})
        with timed(
            self._logger,
            "load_market_factors",
            valuation_date=valuation_date.isoformat(),
            factor_codes=tuple(codes),
        ):
            frame = self._db.query_polars("SELECT * FROM fct_market_factors")
            if frame.is_empty():
                return frame.select(["factor_code", "value"])
            return (
                frame.filter(
                    (pl.col("valuation_date") <= valuation_date)
                    & pl.col("factor_code").is_in(list(codes))
                )
                .sort("valuation_date", descending=True)
                .unique(subset=["factor_code"], keep="first")
                .select(["factor_code", "value"])
            )


__all__ = ["RegulatoryDataRepository"]
