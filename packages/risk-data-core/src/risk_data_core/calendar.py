"""Santiago (Chile) business-day calendar.

Offline-first: recurring holidays are computed from the official legal rules and
singular holidays come from :mod:`risk_data_core.holiday_data` or the caller.
The default ``banking`` mode is Monday-Friday minus national holidays and the
year-end bank holiday; ``national`` excludes the bank-only holidays.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import polars as pl

from risk_data_core.holiday_data import (
    SINGULAR_HOLIDAYS,
    SOLSTICE_FALLBACK_DAY,
    SOLSTICE_FALLBACK_MONTH,
    SOLSTICE_HOLIDAYS,
)

CalendarMode = Literal["banking", "national"]


@dataclass(frozen=True)
class Holiday:
    """A single Chilean holiday."""

    date: date
    name: str
    scope: str = "national"
    legal_basis: str = ""


def easter_sunday(year: int) -> date:
    """Return Easter Sunday for ``year`` (anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _move_to_monday(day: date) -> date:
    """Ley 19.668: Tue/Wed/Thu -> same-week Monday; Friday -> next Monday."""
    weekday = day.weekday()
    if weekday in (1, 2, 3):
        return day - timedelta(days=weekday)
    if weekday == 4:
        return day + timedelta(days=3)
    return day


def _move_october_31(day: date) -> date:
    """Ley 20.299: October 31 moves to the preceding Friday on Tue/Wed."""
    if day.weekday() == 1:
        return day - timedelta(days=4)
    if day.weekday() == 2:
        return day - timedelta(days=5)
    return day


def _solstice_holiday(year: int) -> date:
    return SOLSTICE_HOLIDAYS.get(
        year,
        date(year, SOLSTICE_FALLBACK_MONTH, SOLSTICE_FALLBACK_DAY),
    )


def _recurring_holidays(year: int) -> list[Holiday]:
    holidays = [
        Holiday(date(year, 1, 1), "Año Nuevo", "national", "Ley 2.977 / 19.973"),
        Holiday(date(year, 5, 1), "Día Nacional del Trabajo", "national", "Ley 2.977 / 19.973"),
        Holiday(date(year, 5, 21), "Día de las Glorias Navales", "national", "Ley 2.977"),
        Holiday(date(year, 7, 16), "Virgen del Carmen", "national", "Ley 20.148"),
        Holiday(date(year, 8, 15), "Asunción de la Virgen", "national", "Ley 2.977"),
        Holiday(date(year, 9, 18), "Independencia Nacional", "national", "Ley 2.977 / 19.973"),
        Holiday(
            date(year, 9, 19),
            "Día de las Glorias del Ejército",
            "national",
            "Ley 2.977 / 19.973",
        ),
        Holiday(date(year, 11, 1), "Día de Todos los Santos", "national", "Ley 2.977"),
        Holiday(date(year, 12, 8), "Inmaculada Concepción", "national", "Ley 2.977"),
        Holiday(date(year, 12, 25), "Navidad", "national", "Ley 2.977 / 19.973"),
        Holiday(
            _solstice_holiday(year),
            "Día Nacional de los Pueblos Indígenas",
            "national",
            "Ley 21.357",
        ),
        Holiday(
            _move_to_monday(date(year, 6, 29)),
            "San Pedro y San Pablo",
            "national",
            "Ley 19.668",
        ),
        Holiday(
            _move_to_monday(date(year, 10, 12)),
            "Encuentro de Dos Mundos",
            "national",
            "Ley 19.668",
        ),
        Holiday(
            _move_october_31(date(year, 10, 31)),
            "Día Nacional de las Iglesias Evangélicas y Protestantes",
            "national",
            "Ley 20.299",
        ),
    ]
    easter = easter_sunday(year)
    holidays.append(Holiday(easter - timedelta(days=2), "Viernes Santo", "national", "Ley 2.977"))
    holidays.append(Holiday(easter - timedelta(days=1), "Sábado Santo", "national", "Ley 2.977"))
    san_lunes = date(year, 1, 2)
    if san_lunes.weekday() == 0:
        holidays.append(Holiday(san_lunes, "San Lunes", "national", "Ley 20.983"))
    san_viernes = date(year, 9, 17)
    if san_viernes.weekday() == 4:
        holidays.append(Holiday(san_viernes, "San Viernes", "national", "Ley 20.983"))
    return holidays


class SantiagoCalendar:
    """Chilean business-day calendar for Santiago."""

    def __init__(
        self,
        mode: CalendarMode = "banking",
        extra_holidays: Mapping[int, Iterable[Holiday]] | None = None,
    ) -> None:
        if mode not in ("banking", "national"):
            msg = f"invalid calendar mode: {mode!r}"
            raise ValueError(msg)
        self.mode: CalendarMode = mode
        self._extra: dict[int, tuple[Holiday, ...]] = {
            year: tuple(items) for year, items in (extra_holidays or {}).items()
        }
        self._cache: dict[int, tuple[Holiday, ...]] = {}
        self._date_cache: dict[int, frozenset[date]] = {}

    def holiday_objects(self, year: int) -> tuple[Holiday, ...]:
        """Return the holidays for ``year`` as :class:`Holiday` objects."""
        if year in self._cache:
            return self._cache[year]
        allowed_scopes = {"national", "metropolitana"}
        if self.mode == "banking":
            allowed_scopes.add("banking")
        items = _recurring_holidays(year)
        for row in SINGULAR_HOLIDAYS.get(year, ()):
            items.append(Holiday(row[0], row[1], row[2], row[3]))
        items.extend(self._extra.get(year, ()))
        if self.mode == "banking":
            items.append(
                Holiday(
                    date(year, 12, 31),
                    "Feriado bancario de fin de año",
                    "banking",
                    "Ley 21.791",
                )
            )
        unique = {holiday.date: holiday for holiday in items if holiday.scope in allowed_scopes}
        result = tuple(sorted(unique.values(), key=lambda holiday: holiday.date))
        self._cache[year] = result
        return result

    def holidays(self, year: int) -> pl.DataFrame:
        """Return the holidays for ``year`` as a Polars frame."""
        objects = self.holiday_objects(year)
        return pl.DataFrame(
            {
                "date": [holiday.date for holiday in objects],
                "name": [holiday.name for holiday in objects],
                "scope": [holiday.scope for holiday in objects],
                "legal_basis": [holiday.legal_basis for holiday in objects],
            },
            schema={
                "date": pl.Date,
                "name": pl.String,
                "scope": pl.String,
                "legal_basis": pl.String,
            },
        )

    def _holiday_dates(self, year: int) -> frozenset[date]:
        if year not in self._date_cache:
            self._date_cache[year] = frozenset(
                holiday.date for holiday in self.holiday_objects(year)
            )
        return self._date_cache[year]

    def is_business_day(self, day: date) -> bool:
        """Return ``True`` when ``day`` is a Santiago business day."""
        if day.weekday() >= 5:
            return False
        return day not in self._holiday_dates(day.year)

    def business_days(self, year: int) -> pl.DataFrame:
        """Return every business day of ``year`` as a Polars frame."""
        holidays = self._holiday_dates(year)
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        business = [day for day in days if day.weekday() < 5 and day not in holidays]
        return pl.DataFrame({"date": business}, schema={"date": pl.Date})

    def add_business_days(self, day: date, count: int) -> date:
        """Add ``count`` business days to ``day``.

        ``count=1`` returns the next business day strictly after ``day``.
        ``count=0`` returns ``day`` if it is a business day, else the next one.
        """
        if count == 0:
            return day if self.is_business_day(day) else self.next_business_day(day)
        step = 1 if count > 0 else -1
        remaining = abs(count)
        current = day
        while remaining:
            current += timedelta(days=step)
            if self.is_business_day(current):
                remaining -= 1
        return current

    def next_business_day(self, day: date) -> date:
        """Return the next business day strictly after ``day``."""
        return self.add_business_days(day, 1)

    def previous_business_day(self, day: date) -> date:
        """Return the previous business day strictly before ``day``."""
        return self.add_business_days(day, -1)

    def business_days_between(self, start: date, end: date) -> int:
        """Count business days between ``start`` and ``end`` (inclusive)."""
        if start > end:
            start, end = end, start
        count = 0
        current = start
        while current <= end:
            if self.is_business_day(current):
                count += 1
            current += timedelta(days=1)
        return count

    def settlement_date(self, trade_date: date, t_plus: int = 0) -> date:
        """Return the settlement date for ``trade_date`` at ``T+t_plus``."""
        if t_plus == 0:
            return (
                trade_date
                if self.is_business_day(trade_date)
                else self.next_business_day(trade_date)
            )
        return self.add_business_days(trade_date, t_plus)


__all__ = ["CalendarMode", "Holiday", "SantiagoCalendar", "easter_sunday"]
