"""Tablas normativas del Capitulo 21-7 de la RAN de la CMF.

Las tablas de la CMF se persisten como JSON en este directorio para evitar
depender del PDF original. Este modulo las carga y expone estructuras tipadas,
ademas de los helpers de clasificacion (banda temporal en dias, tipo de moneda,
id de moneda y PRM).

Fuentes:

- Tabla 1  : Monedas y unidades de cuenta.
- Tabla 114: Exposiciones sujetas a riesgo general y especifico de tasas.
- Tabla 115: Riesgo especifico de tasa de interes.
- Tabla 116: Banda temporal de riesgo de tasa de interes.
- PRM      : RAN 21-7 / Circular N.2282 (Tablas 2 y 3).
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from functools import cache
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parent


@cache
def _load(name: str) -> dict[str, Any]:
    with (_CONFIG_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_tabla_1() -> dict[str, Any]:
    return _load("tabla_1_monedas.json")


def _load_tabla_114() -> dict[str, Any]:
    return _load("tabla_114_exposiciones.json")


def _load_tabla_115() -> dict[str, Any]:
    return _load("tabla_115_riesgo_especifico.json")


def _load_tabla_116() -> dict[str, Any]:
    return _load("tabla_116_bandas_temporales.json")


def _load_prm() -> dict[str, Any]:
    return _load("prm_riesgo_general.json")


# ---------------------------------------------------------------------------
# Tabla 1: monedas y unidades de cuenta
# ---------------------------------------------------------------------------
TABLE_1_MONEDAS: dict[str, dict[str, str]] = _load_tabla_1()["monedas"]
CURRENCY_ALIASES: dict[str, str] = _load_tabla_1()["currency_aliases"]

# ---------------------------------------------------------------------------
# Tabla 114: exposiciones
# ---------------------------------------------------------------------------
TABLE_114_EXPOSICIONES: dict[str, dict[str, str]] = _load_tabla_114()["exposiciones"]

# ---------------------------------------------------------------------------
# Tabla 115: riesgo especifico
# ---------------------------------------------------------------------------
TABLE_115_RIESGO_ESPECIFICO: dict[str, dict[str, str]] = _load_tabla_115()["riesgo_especifico"]

# ---------------------------------------------------------------------------
# Tabla 116: bandas temporales (13 bandas, metodo de vencimiento)
# ---------------------------------------------------------------------------
_BANDAS_RAW: dict[str, dict[str, Any]] = _load_tabla_116()["bandas"]

BAND_CODES: tuple[str, ...] = tuple(f"{index:02d}" for index in range(1, 14))

# Limite superior en dias de cada banda (banda 13 = infinito). Se mantiene el
# diccionario explicito solicitado: {"01": 31, "02": 93, ...}.
BAND_DAYS_UPPER: dict[str, float] = {
    code: (math.inf if raw["limite_superior_dias"] is None else raw["limite_superior_dias"])
    for code, raw in _BANDAS_RAW.items()
}

# Umbrales ordenados (sin el infinito) para ``bisect_left``.
BAND_THRESHOLDS: tuple[int, ...] = tuple(
    int(raw["limite_superior_dias"])
    for raw in _BANDAS_RAW.values()
    if raw["limite_superior_dias"] is not None
)

ZONA_POR_BANDA: dict[str, int] = {code: int(raw["zona"]) for code, raw in _BANDAS_RAW.items()}

# ---------------------------------------------------------------------------
# PRM de riesgo general y factores de descalce
# ---------------------------------------------------------------------------
_PRM_RAW = _load_prm()
PRM: dict[str, dict[str, float]] = {
    tipo: {band: float(value) for band, value in values.items()}
    for tipo, values in _PRM_RAW["prm"].items()
}
FACTORES_DESCALCE: dict[str, Any] = _PRM_RAW["factores_descalce"]
VERTICAL_BETA: float = float(FACTORES_DESCALCE["vertical_beta"])
HORIZONTAL_INTRA_ZONA: dict[int, float] = {
    int(zone): float(value) for zone, value in FACTORES_DESCALCE["horizontal_intra_zona"].items()
}
HORIZONTAL_INTER_ZONA: dict[str, float] = {
    pair: float(value) for pair, value in FACTORES_DESCALCE["horizontal_inter_zona"].items()
}
MULTIPLICADOR_APRM: float = float(_PRM_RAW["multiplicador_aprm"])
FACTOR_FX: float = float(_PRM_RAW["factor_fx"])

TIPOS_MONEDA: tuple[str, ...] = ("CLP", "UR", "MX")


def band_from_days(days: int) -> str:
    """Devuelve el codigo de banda ``"01".."13"`` para ``days`` dias.

    Usa ``bisect_left`` sobre :data:`BAND_THRESHOLDS` (limites superiores
    inclusivos). Los dias negativos (flujo vencido) se rechazan.
    """
    if days < 0:
        raise ValueError(f"days must be non-negative, got {days}")
    return BAND_CODES[bisect_left(BAND_THRESHOLDS, days)]


def zona_for_band(band: str) -> int:
    """Devuelve la zona (1, 2 o 3) de una banda temporal."""
    return ZONA_POR_BANDA[band]


def tipo_moneda_for(currency: str) -> str:
    """Clasifica una moneda en ``CLP``, ``UR`` o ``MX``.

    ``CLP`` -> no reajustable; ``UF``/``CLF``/``IVP``/``UTM``/``IPC``/``UR`` ->
    reajustable (UR); cualquier otra -> extranjera (MX).
    """
    code = str(currency).strip().upper()
    if code in {"CLP", "999"}:
        return "CLP"
    if code in {
        "UF",
        "CLF",
        "IVP",
        "UTM",
        "IPC",
        "UR",
        "998",
        "997",
        "996",
        "995",
        "993",
    }:
        return "UR"
    return "MX"


def id_moneda_for(currency: str) -> str:
    """Devuelve el codigo de la Tabla 1 (CMF) para ``currency``."""
    code = str(currency).strip().upper()
    if code in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[code]
    padded = code.zfill(3)
    if padded in TABLE_1_MONEDAS:
        return padded
    return "800"


def prm_for(tipo_moneda: str, band: str) -> float:
    """PRM de riesgo general para ``tipo_moneda`` y ``band``."""
    return PRM[tipo_moneda][band]


__all__ = [
    "BAND_CODES",
    "BAND_DAYS_UPPER",
    "BAND_THRESHOLDS",
    "CURRENCY_ALIASES",
    "FACTORES_DESCALCE",
    "FACTOR_FX",
    "HORIZONTAL_INTER_ZONA",
    "HORIZONTAL_INTRA_ZONA",
    "MULTIPLICADOR_APRM",
    "PRM",
    "TABLE_1_MONEDAS",
    "TABLE_114_EXPOSICIONES",
    "TABLE_115_RIESGO_ESPECIFICO",
    "TIPOS_MONEDA",
    "VERTICAL_BETA",
    "ZONA_POR_BANDA",
    "band_from_days",
    "id_moneda_for",
    "prm_for",
    "tipo_moneda_for",
    "zona_for_band",
]
