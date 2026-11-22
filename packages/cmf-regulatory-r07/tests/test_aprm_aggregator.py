"""Pruebas de consolidacion de cargos y multiplicador regulatorio 12.5."""

from __future__ import annotations

import pytest
from cmf_regulatory_r07.domain.aprm_aggregator import aggregate_aprm


def test_single_component_multiplied_by_12_5() -> None:
    result = aggregate_aprm({"riesgo_general_tasas": 100.0})
    assert result.total_charge == pytest.approx(100.0)
    assert result.total_aprm == pytest.approx(1_250.0)
    assert result.multiplier == pytest.approx(12.5)
    assert result.components[0].aprm == pytest.approx(1_250.0)


def test_multiple_components_preserve_order() -> None:
    result = aggregate_aprm({"riesgo_general_tasas": 100.0, "riesgo_moneda": 50.0})
    assert [component.name for component in result.components] == [
        "riesgo_general_tasas",
        "riesgo_moneda",
    ]
    assert result.total_charge == pytest.approx(150.0)
    assert result.total_aprm == pytest.approx(1_875.0)


def test_empty_charges() -> None:
    result = aggregate_aprm({})
    assert result.total_charge == pytest.approx(0.0)
    assert result.total_aprm == pytest.approx(0.0)


def test_custom_multiplier() -> None:
    result = aggregate_aprm({"x": 10.0}, multiplier=10.0)
    assert result.total_aprm == pytest.approx(100.0)
