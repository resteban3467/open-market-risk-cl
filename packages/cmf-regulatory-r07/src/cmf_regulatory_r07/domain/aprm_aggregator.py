"""Consolidacion de cargos de capital y calculo del APRM total.

Cada cargo (riesgo general de tasas, riesgo de moneda extranjera, ...) se
multiplica por el factor regulatorio ``12.5`` para expresarlo como activos
ponderados por riesgo de mercado (APRM) en CLP.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from cmf_regulatory_r07.config.ran21_7_tables import MULTIPLICADOR_APRM


@dataclass(frozen=True)
class AprmComponent:
    """Un componente de capital y su equivalente en APRM."""

    name: str
    charge: float
    aprm: float


@dataclass(frozen=True)
class AprmResult:
    """Consolidacion de componentes y APRM total."""

    components: tuple[AprmComponent, ...] = field(default_factory=tuple)
    total_charge: float = 0.0
    total_aprm: float = 0.0
    multiplier: float = MULTIPLICADOR_APRM

    def as_dict(self) -> dict[str, object]:
        return {
            "components": [
                {"name": c.name, "charge": c.charge, "aprm": c.aprm} for c in self.components
            ],
            "total_charge": self.total_charge,
            "total_aprm": self.total_aprm,
            "multiplier": self.multiplier,
        }


def aggregate_aprm(
    charges: Mapping[str, float], *, multiplier: float = MULTIPLICADOR_APRM
) -> AprmResult:
    """Consolida ``charges`` (nombre -> cargo en CLP) y aplica ``multiplier``.

    El orden de ``charges`` se preserva para que el reporte sea determinista.
    """
    components = tuple(
        AprmComponent(name=name, charge=charge, aprm=charge * multiplier)
        for name, charge in charges.items()
    )
    total_charge = sum(component.charge for component in components)
    return AprmResult(
        components=components,
        total_charge=total_charge,
        total_aprm=total_charge * multiplier,
        multiplier=multiplier,
    )


__all__ = ["AprmComponent", "AprmResult", "aggregate_aprm"]
