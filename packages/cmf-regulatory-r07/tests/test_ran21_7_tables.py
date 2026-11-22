"""Pruebas de las tablas normativas (Tabla 1, 114, 115, 116 y PRM)."""

from __future__ import annotations

import pytest
from cmf_regulatory_r07.config.ran21_7_tables import (
    BAND_CODES,
    BAND_THRESHOLDS,
    PRM,
    TABLE_1_MONEDAS,
    TABLE_114_EXPOSICIONES,
    TABLE_115_RIESGO_ESPECIFICO,
    band_from_days,
    id_moneda_for,
    prm_for,
    tipo_moneda_for,
    zona_for_band,
)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "01"),
        (31, "01"),
        (32, "02"),
        (93, "02"),
        (94, "03"),
        (186, "03"),
        (187, "04"),
        (365, "04"),
        (366, "05"),
        (730, "05"),
        (731, "06"),
        (1095, "06"),
        (1096, "07"),
        (1460, "07"),
        (1461, "08"),
        (1825, "08"),
        (1826, "09"),
        (2555, "09"),
        (2556, "10"),
        (3650, "10"),
        (3651, "11"),
        (5475, "11"),
        (5476, "12"),
        (7300, "12"),
        (7301, "13"),
        (20_000, "13"),
    ],
)
def test_band_from_days_boundaries(days: int, expected: str) -> None:
    assert band_from_days(days) == expected


def test_band_from_days_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        band_from_days(-1)


def test_thirteen_bands_and_thresholds() -> None:
    assert len(BAND_CODES) == 13
    assert tuple(f"{index:02d}" for index in range(1, 14)) == BAND_CODES
    assert BAND_THRESHOLDS == (31, 93, 186, 365, 730, 1095, 1460, 1825, 2555, 3650, 5475, 7300)


@pytest.mark.parametrize(
    ("band", "zona"),
    [("01", 1), ("04", 1), ("05", 2), ("08", 2), ("09", 3), ("13", 3)],
)
def test_zone_assignment(band: str, zona: int) -> None:
    assert zona_for_band(band) == zona


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("CLP", "CLP"), ("UF", "UR"), ("CLF", "UR"), ("IPC", "UR"), ("USD", "MX"), ("EUR", "MX")],
)
def test_tipo_moneda(currency: str, expected: str) -> None:
    assert tipo_moneda_for(currency) == expected


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("CLP", "999"), ("UF", "998"), ("USD", "013"), ("EUR", "142"), ("JPY", "072"), ("XYZ", "800")],
)
def test_id_moneda(currency: str, expected: str) -> None:
    assert id_moneda_for(currency) == expected


def test_prm_values() -> None:
    assert prm_for("CLP", "05") == pytest.approx(0.0125)
    assert prm_for("CLP", "13") == pytest.approx(0.0607)
    assert prm_for("UR", "13") == pytest.approx(0.0475)
    assert prm_for("MX", "01") == pytest.approx(0.0)
    assert set(PRM) == {"CLP", "UR", "MX"}
    assert all(len(series) == 13 for series in PRM.values())


def test_tables_have_expected_entries() -> None:
    assert TABLE_1_MONEDAS["999"]["pais"] == "Chile"
    assert TABLE_1_MONEDAS["013"]["moneda"] == "Dolar"
    assert TABLE_114_EXPOSICIONES["02"]["exposicion"] == "Deuda Gobierno de Chile"
    assert TABLE_114_EXPOSICIONES["18"]["posicion"] == "ACTIVE"
    assert TABLE_114_EXPOSICIONES["31"]["posicion"] == "PASSIVE"
    assert TABLE_115_RIESGO_ESPECIFICO["08"]["emisor"] == "Estado de Chile o Banco Central de Chile"
