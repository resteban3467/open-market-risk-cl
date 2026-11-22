"""Pruebas del motor GIRR (posicion neta, vertical y ajustes horizontales)."""

from __future__ import annotations

from typing import cast

import polars as pl
import pytest
from cmf_regulatory_r07.domain.girr_engine import compute_girr

_SCHEMA = {
    "tipo_moneda": pl.String,
    "band_code": pl.String,
    "is_active": pl.Boolean,
    "exposicion_clp": pl.Float64,
    "prm": pl.Float64,
}


def _s02(rows: list[tuple[str, str, bool, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=_SCHEMA, orient="row")


def test_empty_returns_zero() -> None:
    result = compute_girr(pl.DataFrame(schema=_SCHEMA))
    assert result.total_charge == 0.0
    assert result.by_currency == ()


def test_single_long_position() -> None:
    result = compute_girr(_s02([("CLP", "05", True, 1_000_000_000.0, 1.0)]))
    assert result.net_weighted_position == pytest.approx(1_000_000_000.0)
    assert result.vertical == pytest.approx(0.0)
    assert result.horizontal == pytest.approx(0.0)
    assert result.total_charge == pytest.approx(1_000_000_000.0)


def test_prm_scales_weighted_position() -> None:
    result = compute_girr(_s02([("CLP", "05", True, 1_000_000_000.0, 0.0125)]))
    assert result.net_weighted_position == pytest.approx(12_500_000.0)
    assert result.total_charge == pytest.approx(12_500_000.0)


def test_intra_band_match_applies_vertical_and_zone_horizontal() -> None:
    # Misma banda (zona 2): 100 largo vs 100 corto, PRM=1.
    result = compute_girr(
        _s02(
            [
                ("CLP", "05", True, 100.0, 1.0),
                ("CLP", "05", False, 100.0, 1.0),
            ]
        )
    )
    # neta = 0; vertical = 10% * 100 = 10; horizontal zona 2 = 30% * 100 = 30.
    assert result.net_weighted_position == pytest.approx(0.0)
    assert result.vertical == pytest.approx(10.0)
    assert result.horizontal == pytest.approx(30.0)
    assert result.total_charge == pytest.approx(40.0)


def test_intra_zone_match_across_bands() -> None:
    # Zona 2, bandas distintas: 100 largo vs 100 corto, PRM=1.
    result = compute_girr(
        _s02(
            [
                ("CLP", "05", True, 100.0, 1.0),
                ("CLP", "07", False, 100.0, 1.0),
            ]
        )
    )
    assert result.net_weighted_position == pytest.approx(0.0)
    assert result.vertical == pytest.approx(0.0)
    assert result.horizontal == pytest.approx(30.0)
    assert result.total_charge == pytest.approx(30.0)


def test_inter_zone_adjacent_uses_40_percent() -> None:
    # Zona 1 vs zona 2: residuales opuestos de 100 -> 40% de descalce.
    result = compute_girr(
        _s02(
            [
                ("CLP", "04", True, 100.0, 1.0),
                ("CLP", "05", False, 100.0, 1.0),
            ]
        )
    )
    assert result.net_weighted_position == pytest.approx(0.0)
    assert result.horizontal == pytest.approx(40.0)
    assert result.total_charge == pytest.approx(40.0)


def test_inter_zone_1_3_uses_100_percent() -> None:
    result = compute_girr(
        _s02(
            [
                ("CLP", "04", True, 100.0, 1.0),
                ("CLP", "09", False, 100.0, 1.0),
            ]
        )
    )
    assert result.net_weighted_position == pytest.approx(0.0)
    assert result.horizontal == pytest.approx(100.0)
    assert result.total_charge == pytest.approx(100.0)


def test_currencies_are_aggregated_independently() -> None:
    result = compute_girr(
        _s02(
            [
                ("CLP", "05", True, 100.0, 1.0),
                ("MX", "05", True, 100.0, 1.0),
            ]
        )
    )
    assert result.net_weighted_position == pytest.approx(200.0)
    assert {currency.tipo_moneda for currency in result.by_currency} == {"CLP", "MX"}
    assert result.total_charge == pytest.approx(200.0)


def test_as_dict_roundtrip() -> None:
    result = compute_girr(_s02([("CLP", "05", True, 100.0, 1.0)]))
    payload = result.as_dict()
    assert payload["total_charge"] == pytest.approx(100.0)
    currencies = cast(list[dict[str, object]], payload["by_currency"])
    assert currencies[0]["tipo_moneda"] == "CLP"
