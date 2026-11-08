from datetime import date
from typing import cast

import pytest
from risk_data_core.calendar import CalendarMode, Holiday, SantiagoCalendar, easter_sunday


def test_easter_sunday_known_years() -> None:
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_2026_national_holidays() -> None:
    calendar = SantiagoCalendar(mode="national")
    holidays = {holiday.date: holiday.name for holiday in calendar.holiday_objects(2026)}

    assert holidays[date(2026, 1, 1)] == "Año Nuevo"
    assert holidays[date(2026, 4, 3)] == "Viernes Santo"
    assert holidays[date(2026, 4, 4)] == "Sábado Santo"
    assert holidays[date(2026, 5, 1)] == "Día Nacional del Trabajo"
    assert holidays[date(2026, 5, 21)] == "Día de las Glorias Navales"
    assert holidays[date(2026, 6, 21)] == "Día Nacional de los Pueblos Indígenas"
    assert holidays[date(2026, 6, 29)] == "San Pedro y San Pablo"
    assert holidays[date(2026, 7, 16)] == "Virgen del Carmen"
    assert holidays[date(2026, 8, 15)] == "Asunción de la Virgen"
    assert holidays[date(2026, 9, 18)] == "Independencia Nacional"
    assert holidays[date(2026, 9, 19)] == "Día de las Glorias del Ejército"
    assert holidays[date(2026, 10, 12)] == "Encuentro de Dos Mundos"
    assert holidays[date(2026, 10, 31)] == "Día Nacional de las Iglesias Evangélicas y Protestantes"
    assert holidays[date(2026, 11, 1)] == "Día de Todos los Santos"
    assert holidays[date(2026, 12, 8)] == "Inmaculada Concepción"
    assert holidays[date(2026, 12, 25)] == "Navidad"
    assert date(2026, 12, 31) not in holidays


def test_2027_movable_holidays_and_san_viernes() -> None:
    calendar = SantiagoCalendar(mode="national")
    holidays = {holiday.date for holiday in calendar.holiday_objects(2027)}

    assert date(2027, 6, 28) in holidays  # June 29 (Tue) -> Monday
    assert date(2027, 10, 11) in holidays  # October 12 (Tue) -> Monday
    assert date(2027, 9, 17) in holidays  # San Viernes


def test_2023_san_lunes_and_october_31_move() -> None:
    calendar = SantiagoCalendar(mode="national")
    holidays = {holiday.date for holiday in calendar.holiday_objects(2023)}

    assert date(2023, 1, 2) in holidays  # January 1 Sunday -> San Lunes
    assert date(2023, 10, 27) in holidays  # October 31 Tuesday -> preceding Friday
    assert date(2023, 10, 31) not in holidays


def test_banking_mode_includes_year_end_bank_holiday() -> None:
    national = SantiagoCalendar(mode="national")
    banking = SantiagoCalendar(mode="banking")

    assert national.is_business_day(date(2026, 12, 31))
    assert not banking.is_business_day(date(2026, 12, 31))
    assert date(2026, 12, 31) not in {holiday.date for holiday in national.holiday_objects(2026)}
    assert date(2026, 12, 31) in {holiday.date for holiday in banking.holiday_objects(2026)}


def test_business_day_counts_2026() -> None:
    national = SantiagoCalendar(mode="national")
    banking = SantiagoCalendar(mode="banking")

    assert national.business_days(2026).height == 251
    assert banking.business_days(2026).height == 250


def test_add_between_and_settlement_conventions() -> None:
    calendar = SantiagoCalendar(mode="banking")

    assert calendar.add_business_days(date(2026, 1, 1), 1) == date(2026, 1, 2)
    assert calendar.add_business_days(date(2026, 1, 2), 1) == date(2026, 1, 5)
    assert calendar.next_business_day(date(2026, 1, 1)) == date(2026, 1, 2)
    assert calendar.previous_business_day(date(2026, 1, 5)) == date(2026, 1, 2)

    assert calendar.business_days_between(date(2026, 1, 1), date(2026, 1, 5)) == 2
    assert calendar.settlement_date(date(2026, 1, 2), 0) == date(2026, 1, 2)
    assert calendar.settlement_date(date(2026, 1, 2), 1) == date(2026, 1, 5)
    assert calendar.settlement_date(date(2026, 1, 1), 0) == date(2026, 1, 2)


def test_singular_holiday_injection() -> None:
    extra = {2026: (Holiday(date(2026, 7, 1), "Feriado de prueba", "national"),)}
    calendar = SantiagoCalendar(extra_holidays=extra)

    assert not calendar.is_business_day(date(2026, 7, 1))
    assert date(2026, 7, 1) in {holiday.date for holiday in calendar.holiday_objects(2026)}


def test_bundled_singular_holiday_is_applied() -> None:
    calendar = SantiagoCalendar(mode="national")

    assert not calendar.is_business_day(date(2017, 4, 19))  # Censo 2017
    assert not calendar.is_business_day(date(2018, 1, 16))  # RM papal visit


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid calendar mode"):
        SantiagoCalendar(mode=cast(CalendarMode, "administrative"))
