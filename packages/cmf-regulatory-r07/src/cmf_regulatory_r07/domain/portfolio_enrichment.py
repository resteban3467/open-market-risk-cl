"""Construccion del portafolio enriquecido para la seccion 02 del R07.

Toma el portafolio "real" (``dim_trades`` / ``dim_legs`` / ``fct_valued_cashflows``)
y genera ``r07_s02`` (riesgo general de tasas de interes) agregando las columnas
auxiliares que exige la norma:

- ``book``: libro del trade (``TRADING``/``BANKING``), usado para el filtro de
  alcance de la seccion.
- ``instrument_kind``: clasificacion de derivados (tasa / moneda / inflacion).
- ``r07_temp_band_days``: dias al vencimiento (fija + bullet = vencimiento de la
  pata; fija + no bullet = fecha de cada flujo; flotante = proximo reset).
- ``band_code``: ``"01".."13"`` via ``bisect_left`` sobre la Tabla 116.
- ``id_moneda`` / ``tipo_moneda``: Tabla 1 y clasificacion CLP/UR/MX.
- ``exposicion_code``: Tabla 114 (mapeo producto / pata / activo-pasivo).
- ``exposicion_clp``: valor de mercado convertido a CLP con tasas BCCh.
- ``prm``: ponderador de riesgo general por tipo de moneda y banda.

Fijo vs flotante es un atributo de la pata (``dim_legs.rate_type``) y el codigo
de tasa (``reference_index``) proviene del portafolio, no de la norma.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import polars as pl

from cmf_regulatory_r07.config.ran21_7_tables import (
    ZONA_POR_BANDA,
    band_from_days,
    id_moneda_for,
    prm_for,
    tipo_moneda_for,
)
from cmf_regulatory_r07.config.scope import BOOKS_TASAS

# Tenor (en dias) del proximo reset segun el codigo de tasa del portafolio.
RESET_TENOR_DAYS: dict[str, int] = {
    "OVERNIGHT": 1,
    "SOFR": 1,
    "TIB": 1,
    "TPM": 1,
    "TAB90": 90,
    "TAB160": 160,
    "TAB180": 180,
    "ICP": 31,
    "IPC": 31,
    "UF": 31,
}

# Categorias de contraparte usadas para mapear a la Tabla 114.
_CENTRAL_BANK = {"BCCH", "BANCO CENTRAL", "BANCO CENTRAL DE CHILE"}
_SOVEREIGN = {"TREASURY", "TESORERIA", "GOBIERNO", "GOBIERNO DE CHILE", "ESTADO"}
_FOREIGN_GOV = {"FOREIGN GOVERNMENT", "GOBIERNO EXTRANJERO", "SOBERANO EXTRANJERO"}
_FOREIGN_BANK = {"FOREIGN BANK", "BANCO EXTRANJERO"}
_LOCAL_BANK = {"BANK", "BANCO", "BANCO LOCAL", "LOCAL BANK"}

# Codigos de la Tabla 114 para derivados por tipo de subyacente:
# (codigo posicion activa, codigo posicion pasiva).
_DERIVATIVE_CODES: dict[str, tuple[str, str]] = {
    "SWAP_TASA": ("18", "31"),
    "SWAP_MONEDA": ("19", "32"),
    "SWAP_INFLACION": ("39", "40"),
    "FORWARD_TASA": ("10", "23"),
    "FORWARD_MONEDA": ("11", "24"),
    "FORWARD_INFLACION": ("12", "25"),
}

S02_SCHEMA: dict[str, Any] = {
    "trade_id": pl.String,
    "leg_id": pl.String,
    "book": pl.String,
    "instrument_type": pl.String,
    "instrument_kind": pl.String,
    "counterparty": pl.String,
    "leg_type": pl.String,
    "currency": pl.String,
    "rate_type": pl.String,
    "is_bullet": pl.Boolean,
    "r07_temp_band_days": pl.Int64,
    "band_code": pl.String,
    "zona": pl.Int64,
    "id_moneda": pl.String,
    "tipo_moneda": pl.String,
    "exposicion_code": pl.String,
    "is_active": pl.Boolean,
    "exposicion_orig": pl.Float64,
    "exposicion_clp": pl.Float64,
    "prm": pl.Float64,
    "exposicion_ponderada": pl.Float64,
}


def reset_tenor_days(reference_index: str | None, next_payment_days: int) -> int:
    """Dias al proximo reset de una pata flotante.

    Busca el codigo de tasa (``SOFR``, ``TAB160``, ``ICP``, ...) en
    :data:`RESET_TENOR_DAYS`; si no lo reconoce, usa los dias al proximo pago.
    """
    code = str(reference_index or "").upper()
    for key, tenor in RESET_TENOR_DAYS.items():
        if key in code:
            return tenor
    return max(int(next_payment_days), 0)


def instrument_kind(instrument_type: str | None, currencies: Sequence[str]) -> str:
    """Clasifica un derivado por su subyacente: tasa, moneda o inflacion."""
    instrument = str(instrument_type or "").upper()
    if instrument not in {"SWAP", "FORWARD"}:
        return "NO_DERIVADO"
    leg_currencies = {str(currency).upper() for currency in currencies}
    if leg_currencies & {"CLF", "UF"}:
        return f"{instrument}_INFLACION"
    if len(leg_currencies) > 1:
        return f"{instrument}_MONEDA"
    return f"{instrument}_TASA"


def exposure_code_for(
    instrument_type: str | None,
    counterparty: str | None,
    leg_type: str | None,
    kind: str | None = None,
) -> str:
    """Mapea un instrumento/pata a un codigo de la Tabla 114 (exposicion)."""
    instrument = str(instrument_type or "").upper()
    party = str(counterparty or "").upper()
    position = str(leg_type or "").upper()
    is_active = position == "ACTIVE"

    if instrument in {"BOND", "SPOT", "DEPOSIT"}:
        if party in _CENTRAL_BANK:
            return "01"
        if party in _SOVEREIGN:
            return "02"
        if party in _FOREIGN_GOV:
            return "05"
        if party in _FOREIGN_BANK:
            return "06"
        if party in _LOCAL_BANK:
            return "08"
        return "04"
    if instrument in {"SWAP", "FORWARD"}:
        active_code, passive_code = _DERIVATIVE_CODES[kind or f"{instrument}_TASA"]
        return active_code if is_active else passive_code
    return "22" if is_active else "36"


def _band_days(row: dict[str, Any]) -> int:
    rate_type = str(row["rate_type"] or "").upper()
    if rate_type != "FIXED":
        return reset_tenor_days(row["reference_index"], row["next_payment_days"])
    if row["is_bullet"]:
        return int(row["leg_maturity_days"])
    return int(row["days_to_payment"])


def _empty_s02() -> pl.DataFrame:
    return pl.DataFrame(schema=S02_SCHEMA)


def build_r07_s02(
    *,
    trades: pl.DataFrame,
    legs: pl.DataFrame,
    cashflows: pl.DataFrame,
    fx_factors: dict[str, float],
    reference_date: date,
    books: Sequence[str] = BOOKS_TASAS,
) -> pl.DataFrame:
    """Construye el DataFrame ``r07_s02`` (riesgo general de tasas).

    ``fx_factors`` mapea la moneda del contrato a su factor de conversion a CLP
    (``CLP`` -> 1.0, ``UF`` -> valor UF, ``USD`` -> tipo de cambio USD/CLP, ...).
    ``books`` restringe el calculo a los libros de la seccion (por defecto
    ``TRADING``); los trades de un libro fuera del alcance se excluyen.
    """
    if "book" not in trades.columns:
        msg = "trades must include a 'book' column (portfolio-level attribute)"
        raise ValueError(msg)
    scoped_trades = trades.filter(pl.col("book").is_in(list(books)))
    if cashflows.is_empty():
        return _empty_s02()

    frame = cashflows.join(legs, on="leg_id", how="left", suffix="_leg").join(
        scoped_trades, on="trade_id", how="inner", suffix="_trade"
    )
    if frame.is_empty():
        return _empty_s02()

    # Clasificacion de derivados segun las monedas de las patas del trade.
    trade_currencies = frame.group_by("trade_id").agg(
        pl.col("currency").unique().alias("_trade_currencies")
    )
    frame = frame.join(trade_currencies, on="trade_id", how="left")
    frame = frame.with_columns(
        pl.struct(["instrument_type", "_trade_currencies"])
        .map_elements(
            lambda row: instrument_kind(row["instrument_type"], row["_trade_currencies"]),
            return_dtype=pl.String,
        )
        .alias("instrument_kind")
    )

    # Columnas auxiliares base.
    frame = frame.with_columns(
        (pl.col("payment_date") - pl.lit(reference_date)).dt.total_days().alias("days_to_payment"),
        pl.col("payment_date").max().over("leg_id").alias("leg_maturity_date"),
        pl.col("payment_date").min().over("leg_id").alias("next_payment_date"),
        (pl.col("nominal_amount").n_unique().over("leg_id") == 1).alias("is_bullet"),
    ).with_columns(
        (pl.col("leg_maturity_date") - pl.lit(reference_date))
        .dt.total_days()
        .alias("leg_maturity_days"),
        (pl.col("next_payment_date") - pl.lit(reference_date))
        .dt.total_days()
        .alias("next_payment_days"),
    )

    # Banda temporal (dias) segun fijo/flotante y bullet/no bullet.
    frame = frame.with_columns(
        pl.struct(
            [
                "rate_type",
                "is_bullet",
                "leg_maturity_days",
                "days_to_payment",
                "reference_index",
                "next_payment_days",
            ]
        )
        .map_elements(_band_days, return_dtype=pl.Int64)
        .alias("r07_temp_band_days")
    )

    # Conversion a CLP por flujo (BCCh) antes de agregar.
    frame = frame.with_columns(
        pl.struct(["currency", "present_value_orig_ccy"])
        .map_elements(
            lambda row: (
                float(row["present_value_orig_ccy"])
                * float(fx_factors.get(str(row["currency"]).upper(), 1.0))
            ),
            return_dtype=pl.Float64,
        )
        .alias("pv_clp")
    )

    # Bullet: todos los flujos de la pata caen en la misma banda y se agregan en
    # una sola fila. No bullet: cada flujo conserva su propia fecha de pago.
    s02 = frame.group_by(
        [
            "trade_id",
            "leg_id",
            "book",
            "instrument_type",
            "instrument_kind",
            "counterparty",
            "leg_type",
            "currency",
            "rate_type",
            "is_bullet",
            "r07_temp_band_days",
        ]
    ).agg(
        pl.col("present_value_orig_ccy").sum().alias("exposicion_orig"),
        pl.col("pv_clp").sum().alias("exposicion_clp"),
    )

    s02 = s02.with_columns(
        pl.col("r07_temp_band_days")
        .map_elements(band_from_days, return_dtype=pl.String)
        .alias("band_code"),
        pl.col("currency")
        .map_elements(tipo_moneda_for, return_dtype=pl.String)
        .alias("tipo_moneda"),
        pl.col("currency").map_elements(id_moneda_for, return_dtype=pl.String).alias("id_moneda"),
        pl.struct(["instrument_type", "counterparty", "leg_type", "instrument_kind"])
        .map_elements(
            lambda row: exposure_code_for(
                row["instrument_type"],
                row["counterparty"],
                row["leg_type"],
                row["instrument_kind"],
            ),
            return_dtype=pl.String,
        )
        .alias("exposicion_code"),
        (pl.col("leg_type").str.to_uppercase() == "ACTIVE").alias("is_active"),
    ).with_columns(
        pl.col("band_code").replace_strict(ZONA_POR_BANDA, return_dtype=pl.Int64).alias("zona"),
        pl.struct(["tipo_moneda", "band_code"])
        .map_elements(
            lambda row: prm_for(row["tipo_moneda"], row["band_code"]),
            return_dtype=pl.Float64,
        )
        .alias("prm"),
    )

    s02 = s02.with_columns((pl.col("prm") * pl.col("exposicion_clp")).alias("exposicion_ponderada"))

    return s02.select(list(S02_SCHEMA)).sort(
        ["book", "tipo_moneda", "r07_temp_band_days", "trade_id", "leg_id"]
    )


__all__ = [
    "RESET_TENOR_DAYS",
    "S02_SCHEMA",
    "build_r07_s02",
    "exposure_code_for",
    "instrument_kind",
    "reset_tenor_days",
]
