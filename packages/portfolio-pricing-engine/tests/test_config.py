"""Tests for the parametrized series catalog (Pydantic + YAML)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from portfolio_pricing_engine.config import SeriesEntry, load_catalog
from pydantic import ValidationError

_CATALOG = Path(__file__).resolve().parents[1] / "config" / "series_catalog.yaml"


def test_catalog_loads_and_validates_every_entry() -> None:
    catalog = load_catalog(_CATALOG)
    ids = catalog.by_id()
    assert {"usd_clp", "uf", "tpm", "clp_bcp", "clp_bcu", "usd_treasury", "vix"} <= set(ids)
    assert ids["usd_clp"].adapter == "fx"
    assert ids["usd_clp"].currency_pair == "USD/CLP"
    assert ids["vix"].factor_code == "VIX"
    assert ids["usd_treasury"].bootstrap is True
    treasury = ids["usd_treasury"]
    assert treasury.codes is not None
    assert treasury.codes["30Y"] == "DGS30"


def test_select_filters_by_id() -> None:
    catalog = load_catalog(_CATALOG)
    selected = catalog.select({"vix", "tpm"})
    assert {entry.series_id for entry in selected} == {"vix", "tpm"}


def test_fx_entry_requires_currency_pair() -> None:
    with pytest.raises(ValidationError):
        SeriesEntry(
            series_id="bad",
            source="bcch",
            adapter="fx",
            target_table="fct_fx_rates",
            frequency="DAILY",
            code="X",
            start_date=date(2020, 1, 1),
        )


def test_factor_entry_requires_factor_code() -> None:
    with pytest.raises(ValidationError):
        SeriesEntry(
            series_id="bad",
            source="fred",
            adapter="factor",
            target_table="fct_market_factors",
            frequency="DAILY",
            code="VIXCLS",
            start_date=date(2020, 1, 1),
        )


def test_entry_rejects_mismatched_target_table() -> None:
    with pytest.raises(ValidationError):
        SeriesEntry(
            series_id="bad",
            source="bcch",
            adapter="fx",
            target_table="fct_market_factors",
            frequency="DAILY",
            code="X",
            currency_pair="USD/CLP",
            start_date=date(2020, 1, 1),
        )


def test_curve_entry_requires_codes_and_curve_name() -> None:
    with pytest.raises(ValidationError):
        SeriesEntry(
            series_id="bad",
            source="fred",
            adapter="curve",
            target_table="fct_market_curves",
            frequency="DAILY",
            curve_name="USD_TREASURY",
            start_date=date(2020, 1, 1),
        )


def test_volume_entry_requires_instrument_and_currency() -> None:
    with pytest.raises(ValidationError):
        SeriesEntry(
            series_id="bad",
            source="bcch",
            adapter="volume",
            target_table="fct_banking_volumes",
            frequency="DAILY",
            code="X",
            start_date=date(2020, 1, 1),
        )


def test_quarterly_frequency_is_accepted() -> None:
    entry = SeriesEntry(
        series_id="ok",
        source="bcch",
        adapter="factor",
        target_table="fct_market_factors",
        frequency="QUARTERLY",
        code="X",
        factor_code="GDP",
        start_date=date(2020, 1, 1),
    )
    assert entry.frequency == "QUARTERLY"


def test_catalog_spc_uses_swap_convention() -> None:
    catalog = load_catalog(_CATALOG)
    ids = catalog.by_id()

    spc = ids["clp_spc"]
    assert spc.bootstrap is True
    assert spc.curve_type == "SWAP"
    assert spc.coupon_frequency == "SEMIANNUAL"
    assert spc.codes is not None
    assert {"1D", "30D", "90D", "180D", "1Y", "2Y", "10Y"} <= set(spc.codes)

    spc_uf = ids["clp_spc_uf"]
    assert spc_uf.bootstrap is True
    assert spc_uf.curve_type == "SWAP"
    assert spc_uf.curve_regime == "REAL"
