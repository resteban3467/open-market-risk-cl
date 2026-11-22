"""Pruebas del cargo por riesgo de moneda extranjera (metodo del maximo, 8%)."""

from __future__ import annotations

import pytest
from cmf_regulatory_r07.domain.csr_fx_engine import compute_fx_charge


def test_empty_positions() -> None:
    result = compute_fx_charge({})
    assert result.charge == pytest.approx(0.0)
    assert result.net_long == pytest.approx(0.0)
    assert result.net_short == pytest.approx(0.0)


def test_max_of_long_and_short_at_eight_percent() -> None:
    result = compute_fx_charge({"USD": 1_000.0, "EUR": -400.0})
    assert result.net_long == pytest.approx(1_000.0)
    assert result.net_short == pytest.approx(400.0)
    assert result.charge == pytest.approx(0.08 * 1_000.0)


def test_short_dominates() -> None:
    result = compute_fx_charge({"USD": 300.0, "EUR": -900.0})
    assert result.charge == pytest.approx(0.08 * 900.0)


def test_gold_adds_absolute_value() -> None:
    result = compute_fx_charge({"USD": 1_000.0}, gold_position=-500.0)
    assert result.charge == pytest.approx(0.08 * 1_000.0 + 0.08 * 500.0)


def test_as_dict() -> None:
    payload = compute_fx_charge({"USD": 100.0}).as_dict()
    assert payload["factor"] == pytest.approx(0.08)
    assert payload["charge"] == pytest.approx(8.0)
