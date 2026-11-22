"""Alcance de libros (BOOK) por seccion del Archivo R07.

La RAN 21-7 aplica el riesgo de tasas de interes y de cotizaciones bursatiles al
libro de negociacion, mientras que el riesgo de moneda extranjera y de materias
primas aplican a todo el balance. El alcance se define a nivel de **seccion**
(no de componente de APRM): cada seccion del R07 agrupa varios calculos bajo el
mismo alcance de libros.

- ``s02``: riesgo general y especifico de tasas de interes (+ perfectamente
  compensadas).
- ``s03``: riesgo general y especifico de cotizaciones bursatiles.
- ``s04``: riesgo de materias primas.
- ``s05``: riesgo de moneda extranjera.
"""

from __future__ import annotations

#: Codigo de la seccion de riesgo de tasas de interes (la implementada hoy).
SECTION_TASAS = "s02"

#: Alcance de libros por seccion del Archivo R07.
BOOK_SCOPE: dict[str, tuple[str, ...]] = {
    "s02": ("TRADING",),
    "s03": ("TRADING",),
    "s04": ("TRADING", "BANKING"),
    "s05": ("TRADING", "BANKING"),
}

#: Libros incluidos en la seccion de tasas de interes.
BOOKS_TASAS: tuple[str, ...] = BOOK_SCOPE[SECTION_TASAS]


def books_for_section(section: str) -> tuple[str, ...]:
    """Libros incluidos para una seccion del R07."""
    try:
        return BOOK_SCOPE[section]
    except KeyError:
        msg = f"unknown R07 section: {section!r}"
        raise ValueError(msg) from None


__all__ = ["BOOKS_TASAS", "BOOK_SCOPE", "SECTION_TASAS", "books_for_section"]
