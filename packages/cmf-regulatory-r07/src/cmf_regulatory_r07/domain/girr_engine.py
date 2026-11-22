"""Riesgo general de tasa de interes (GIRR) - RAN 21-7, metodo de vencimiento.

Calculo 100% vectorizado sobre ``r07_s02``: posicion neta ponderada, ajuste
vertical y ajustes horizontales (intra-zona e inter-zona) por tipo de moneda,
siguiendo la cascada de descalces de la norma.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from cmf_regulatory_r07.config.ran21_7_tables import (
    HORIZONTAL_INTER_ZONA,
    HORIZONTAL_INTRA_ZONA,
    VERTICAL_BETA,
    zona_for_band,
)


@dataclass(frozen=True)
class ZoneBreakdown:
    """Desglose del ajuste horizontal dentro de una zona."""

    zona: int
    weighted_long: float
    weighted_short: float
    net_weighted: float
    horizontal_intra: float


@dataclass(frozen=True)
class CurrencyGirr:
    """Cargo GIRR por tipo de moneda."""

    tipo_moneda: str
    net_weighted_position: float
    vertical: float
    horizontal: float
    total: float
    zones: tuple[ZoneBreakdown, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GirrResult:
    """Resultado GIRR consolidado (antes del multiplicador 12.5)."""

    net_weighted_position: float
    vertical: float
    horizontal: float
    total_charge: float
    by_currency: tuple[CurrencyGirr, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "net_weighted_position": self.net_weighted_position,
            "vertical": self.vertical,
            "horizontal": self.horizontal,
            "total_charge": self.total_charge,
            "by_currency": [
                {
                    "tipo_moneda": currency.tipo_moneda,
                    "net_weighted_position": currency.net_weighted_position,
                    "vertical": currency.vertical,
                    "horizontal": currency.horizontal,
                    "total": currency.total,
                    "zones": [
                        {
                            "zona": zone.zona,
                            "weighted_long": zone.weighted_long,
                            "weighted_short": zone.weighted_short,
                            "net_weighted": zone.net_weighted,
                            "horizontal_intra": zone.horizontal_intra,
                        }
                        for zone in currency.zones
                    ],
                }
                for currency in self.by_currency
            ],
        }


def _match(a: float, b: float, factor: float) -> tuple[float, float, float]:
    """Compensa dos posiciones netas de signo opuesto.

    Devuelve ``(cargo, residual_a, residual_b)``.
    """
    if a == 0.0 or b == 0.0 or (a > 0.0) == (b > 0.0):
        return 0.0, a, b
    matched = min(abs(a), abs(b))
    if a > 0.0:
        a -= matched
        b += matched
    else:
        a += matched
        b -= matched
    return factor * matched, a, b


def _currency_charge(
    tipo_moneda: str, bands: dict[str, tuple[float, float, float]]
) -> CurrencyGirr:
    """Cascada GIRR para un tipo de moneda.

    ``bands`` mapea ``band_code -> (long, short, prm)``.
    """
    signed_net = 0.0
    vertical = 0.0
    zone_long = {1: 0.0, 2: 0.0, 3: 0.0}
    zone_short = {1: 0.0, 2: 0.0, 3: 0.0}

    for band, (long_amt, short_amt, prm) in bands.items():
        weighted_long = prm * long_amt
        weighted_short = prm * short_amt
        signed_net += weighted_long - weighted_short
        vertical += VERTICAL_BETA * min(weighted_long, weighted_short)
        zona = zona_for_band(band)
        zone_long[zona] += weighted_long
        zone_short[zona] += weighted_short

    horizontal = 0.0
    residuals: dict[int, float] = {}
    zones: list[ZoneBreakdown] = []
    for zona in (1, 2, 3):
        matched = min(zone_long[zona], zone_short[zona])
        horizontal += HORIZONTAL_INTRA_ZONA[zona] * matched
        residuals[zona] = zone_long[zona] - zone_short[zona]
        zones.append(
            ZoneBreakdown(
                zona=zona,
                weighted_long=zone_long[zona],
                weighted_short=zone_short[zona],
                net_weighted=residuals[zona],
                horizontal_intra=HORIZONTAL_INTRA_ZONA[zona] * matched,
            )
        )

    # Ajustes inter-zona: primero adyacentes (1-2, 2-3) y luego 1-3.
    charge_12, residuals[1], residuals[2] = _match(
        residuals[1], residuals[2], HORIZONTAL_INTER_ZONA["12"]
    )
    charge_23, residuals[2], residuals[3] = _match(
        residuals[2], residuals[3], HORIZONTAL_INTER_ZONA["23"]
    )
    charge_13, residuals[1], residuals[3] = _match(
        residuals[1], residuals[3], HORIZONTAL_INTER_ZONA["13"]
    )
    horizontal += charge_12 + charge_23 + charge_13

    return CurrencyGirr(
        tipo_moneda=tipo_moneda,
        net_weighted_position=abs(signed_net),
        vertical=vertical,
        horizontal=horizontal,
        total=abs(signed_net) + vertical + horizontal,
        zones=tuple(zones),
    )


def compute_girr(r07_s02: pl.DataFrame) -> GirrResult:
    """Calcula el cargo por riesgo general de tasa de interes.

    ``r07_s02`` debe contener ``tipo_moneda``, ``band_code``, ``is_active``,
    ``exposicion_clp`` y ``prm``.
    """
    if r07_s02.is_empty():
        return GirrResult(0.0, 0.0, 0.0, 0.0, ())

    aggregated = r07_s02.group_by(["tipo_moneda", "band_code"]).agg(
        pl.col("exposicion_clp").filter(pl.col("is_active")).sum().alias("long"),
        pl.col("exposicion_clp").filter(~pl.col("is_active")).sum().alias("short"),
        pl.col("prm").first().alias("prm"),
    )

    results: list[CurrencyGirr] = []
    for tipo in aggregated["tipo_moneda"].unique().sort().to_list():
        subset = aggregated.filter(pl.col("tipo_moneda") == tipo)
        bands = {
            row["band_code"]: (
                float(row["long"] or 0.0),
                float(row["short"] or 0.0),
                float(row["prm"]),
            )
            for row in subset.iter_rows(named=True)
        }
        results.append(_currency_charge(str(tipo), bands))

    return GirrResult(
        net_weighted_position=sum(currency.net_weighted_position for currency in results),
        vertical=sum(currency.vertical for currency in results),
        horizontal=sum(currency.horizontal for currency in results),
        total_charge=sum(currency.total for currency in results),
        by_currency=tuple(results),
    )


__all__ = [
    "CurrencyGirr",
    "GirrResult",
    "ZoneBreakdown",
    "compute_girr",
]
