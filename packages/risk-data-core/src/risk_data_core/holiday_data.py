"""Official Chilean holiday rules and data for the Santiago business calendar.

Recurring holidays are derived from the base legislation:

- Ley 2.977 (base holiday law).
- Ley 19.668 (San Pedro y San Pablo / Encuentro de Dos Mundos move to Monday).
- Ley 19.973 (irrenunciable holidays).
- Ley 20.148 (Virgen del Carmen).
- Ley 20.299 (October 31 moves to the preceding Friday on Tue/Wed).
- Ley 20.983 (San Lunes / San Viernes).
- Ley 21.357 (Día Nacional de los Pueblos Indígenas, winter solstice).
- Ley 21.521 / 21.791 (year-end bank holiday).

Singular holidays are stored per year with their scope and legal basis.
"""

from __future__ import annotations

from datetime import date

# Día Nacional de los Pueblos Indígenas (Ley 21.357): austral winter solstice.
# Verified local dates for 2024-2030; June 21 is the documented fallback.
SOLSTICE_HOLIDAYS: dict[int, date] = {
    2024: date(2024, 6, 20),
    2025: date(2025, 6, 20),
    2026: date(2026, 6, 21),
    2027: date(2027, 6, 21),
    2028: date(2028, 6, 20),
    2029: date(2029, 6, 20),
    2030: date(2030, 6, 21),
}
SOLSTICE_FALLBACK_MONTH = 6
SOLSTICE_FALLBACK_DAY = 21

SingularHolidayRow = tuple[date, str, str, str]

# One-off holidays: (date, name, scope, legal_basis). Only verified entries are
# bundled; new years can be injected through SantiagoCalendar(extra_holidays=...)
# or refreshed through the official holiday API adapter.
SINGULAR_HOLIDAYS: dict[int, tuple[SingularHolidayRow, ...]] = {
    2017: ((date(2017, 4, 19), "Censo 2017", "national", "Censo nacional (INE)"),),
    2018: (
        (
            date(2018, 1, 16),
            "Visita del Papa Francisco (RM)",
            "metropolitana",
            "Feriado regional Región Metropolitana",
        ),
    ),
}

__all__ = [
    "SINGULAR_HOLIDAYS",
    "SOLSTICE_FALLBACK_DAY",
    "SOLSTICE_FALLBACK_MONTH",
    "SOLSTICE_HOLIDAYS",
]
