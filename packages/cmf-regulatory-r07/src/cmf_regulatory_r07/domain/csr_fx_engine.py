"""Riesgo de moneda extranjera y (pendiente) riesgo especifico de credito.

Esta iteracion cubre el **riesgo general de tasas** (``r07_s02``). El riesgo
especifico de tasas (CSR) queda explicitamente diferido: la Tabla 115 ya esta
persistida en ``config/tabla_115_riesgo_especifico.json`` para una iteracion
posterior.

El cargo por riesgo de moneda extranjera sigue el metodo estandar del maximo
entre posiciones netas largas y cortas ponderadas por el factor regulatorio
(8% para la Canasta 1), mas el valor absoluto de la posicion neta en oro.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cmf_regulatory_r07.config.ran21_7_tables import FACTOR_FX


@dataclass(frozen=True)
class FxCharge:
    """Resultado del cargo por riesgo de moneda extranjera."""

    net_long: float
    net_short: float
    gold_position: float
    factor: float
    charge: float

    def as_dict(self) -> dict[str, float]:
        return {
            "net_long": self.net_long,
            "net_short": self.net_short,
            "gold_position": self.gold_position,
            "factor": self.factor,
            "charge": self.charge,
        }


def compute_fx_charge(
    net_positions: Mapping[str, float],
    *,
    gold_position: float = 0.0,
    factor: float = FACTOR_FX,
) -> FxCharge:
    """Cargo por riesgo de moneda extranjera (metodo del maximo).

    ``net_positions`` mapea cada moneda extranjera a su posicion neta firmada
    en CLP. El cargo es ``factor * max(sum largas, sum |cortas|)`` mas
    ``factor * |posicion oro|``.
    """
    net_long = sum(value for value in net_positions.values() if value > 0.0)
    net_short = sum(-value for value in net_positions.values() if value < 0.0)
    charge = factor * max(net_long, net_short) + factor * abs(gold_position)
    return FxCharge(
        net_long=net_long,
        net_short=net_short,
        gold_position=gold_position,
        factor=factor,
        charge=charge,
    )


__all__ = ["FxCharge", "compute_fx_charge"]
