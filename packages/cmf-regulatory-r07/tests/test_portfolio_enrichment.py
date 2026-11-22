"""Pruebas del enriquecimiento del portafolio (r07_s02)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from cmf_regulatory_r07.domain.portfolio_enrichment import (
    build_r07_s02,
    exposure_code_for,
    instrument_kind,
    reset_tenor_days,
)

REF = date(2026, 1, 1)


def _trades(rows: list[tuple[str, str, str]], book: str = "TRADING") -> pl.DataFrame:
    return pl.DataFrame(
        [(row[0], row[1], book, row[2]) for row in rows],
        schema={
            "trade_id": pl.String,
            "instrument_type": pl.String,
            "book": pl.String,
            "counterparty": pl.String,
        },
        orient="row",
    )


def _legs(rows: list[tuple[str, str, str, str, str, str | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "leg_id": pl.String,
            "trade_id": pl.String,
            "leg_type": pl.String,
            "currency": pl.String,
            "rate_type": pl.String,
            "reference_index": pl.String,
        },
        orient="row",
    )


def _cashflows(rows: list[tuple[str, str, str, date, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "cashflow_id": pl.String,
            "trade_id": pl.String,
            "leg_id": pl.String,
            "payment_date": pl.Date,
            "nominal_amount": pl.Float64,
            "present_value_orig_ccy": pl.Float64,
        },
        orient="row",
    )


def test_fixed_bullet_uses_leg_maturity_band() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Treasury")]),
        legs=_legs([("l1", "t1", "ACTIVE", "CLP", "FIXED", None)]),
        cashflows=_cashflows(
            [
                ("cf1", "t1", "l1", date(2027, 1, 1), 1_000_000.0, 1_000_000.0),
                ("cf2", "t1", "l1", date(2028, 1, 1), 1_000_000.0, 9_000_000.0),
            ]
        ),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )

    assert s02.height == 1
    row = s02.to_dicts()[0]
    assert row["r07_temp_band_days"] == 730
    assert row["band_code"] == "05"
    assert row["zona"] == 2
    assert row["tipo_moneda"] == "CLP"
    assert row["id_moneda"] == "999"
    assert row["exposicion_code"] == "02"
    assert row["is_bullet"] is True
    assert row["is_active"] is True
    assert row["exposicion_clp"] == pytest.approx(10_000_000.0)
    assert row["prm"] == pytest.approx(0.0125)


def test_fixed_amortizing_uses_each_cashflow_date() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Treasury")]),
        legs=_legs([("l1", "t1", "ACTIVE", "CLP", "FIXED", None)]),
        cashflows=_cashflows(
            [
                ("cf1", "t1", "l1", date(2027, 1, 1), 1_000_000.0, 1_000_000.0),
                ("cf2", "t1", "l1", date(2028, 1, 1), 500_000.0, 500_000.0),
            ]
        ),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )

    assert s02.height == 2
    assert sorted(s02["band_code"].to_list()) == ["04", "05"]
    assert s02["is_bullet"].to_list() == [False, False]


def test_foreign_currency_converts_with_bcch_factor() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Foreign Government")]),
        legs=_legs([("l1", "t1", "ACTIVE", "USD", "FIXED", None)]),
        cashflows=_cashflows([("cf1", "t1", "l1", date(2027, 1, 1), 1_000.0, 1_000.0)]),
        fx_factors={"CLP": 1.0, "USD": 950.0},
        reference_date=REF,
    )

    row = s02.to_dicts()[0]
    assert row["tipo_moneda"] == "MX"
    assert row["id_moneda"] == "013"
    assert row["exposicion_clp"] == pytest.approx(950_000.0)
    assert row["exposicion_code"] == "05"


def test_floating_uses_reset_tenor() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Treasury")]),
        legs=_legs([("l1", "t1", "ACTIVE", "CLP", "FLOAT", "TAB160")]),
        cashflows=_cashflows([("cf1", "t1", "l1", date(2027, 1, 1), 1_000.0, 1_000.0)]),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )

    row = s02.to_dicts()[0]
    assert row["r07_temp_band_days"] == 160
    assert row["band_code"] == "03"


def test_empty_cashflows_returns_empty_schema() -> None:
    s02 = build_r07_s02(
        trades=_trades([]),
        legs=_legs([]),
        cashflows=_cashflows([]),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )
    assert s02.is_empty()
    assert "band_code" in s02.columns


def test_reset_tenor_days() -> None:
    assert reset_tenor_days("TAB160", 999) == 160
    assert reset_tenor_days("SOFR", 999) == 1
    assert reset_tenor_days("ICP", 999) == 31
    assert reset_tenor_days(None, 45) == 45
    assert reset_tenor_days("UNKNOWN", 45) == 45


def test_exposure_code_mapping() -> None:
    assert exposure_code_for("BOND", "Treasury", "ACTIVE") == "02"
    assert exposure_code_for("BOND", "BCCh", "ACTIVE") == "01"
    assert exposure_code_for("BOND", "Bank", "ACTIVE") == "08"
    assert exposure_code_for("BOND", "Corp", "ACTIVE") == "04"
    assert exposure_code_for("BOND", "Foreign Government", "ACTIVE") == "05"
    assert exposure_code_for("SWAP", "X", "ACTIVE") == "18"
    assert exposure_code_for("SWAP", "X", "PASSIVE") == "31"
    assert exposure_code_for("FORWARD", "X", "ACTIVE") == "10"
    assert exposure_code_for("FORWARD", "X", "PASSIVE") == "23"


def test_banking_trades_are_excluded_by_default() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Treasury")], book="BANKING"),
        legs=_legs([("l1", "t1", "ACTIVE", "CLP", "FIXED", None)]),
        cashflows=_cashflows([("cf1", "t1", "l1", date(2027, 1, 1), 1_000_000.0, 1_000_000.0)]),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )
    assert s02.is_empty()


def test_book_column_is_preserved() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "BOND", "Treasury")]),
        legs=_legs([("l1", "t1", "ACTIVE", "CLP", "FIXED", None)]),
        cashflows=_cashflows([("cf1", "t1", "l1", date(2027, 1, 1), 1_000_000.0, 1_000_000.0)]),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )
    assert s02["book"].to_list() == ["TRADING"]
    assert s02["instrument_kind"].to_list() == ["NO_DERIVADO"]


def test_instrument_kind_classification() -> None:
    assert instrument_kind("SWAP", ["CLP", "CLP"]) == "SWAP_TASA"
    assert instrument_kind("SWAP", ["USD", "CLP"]) == "SWAP_MONEDA"
    assert instrument_kind("SWAP", ["UF", "CLP"]) == "SWAP_INFLACION"
    assert instrument_kind("FORWARD", ["USD", "CLP"]) == "FORWARD_MONEDA"
    assert instrument_kind("FORWARD", ["UF", "CLP"]) == "FORWARD_INFLACION"
    assert instrument_kind("BOND", ["CLP"]) == "NO_DERIVADO"


def _two_leg_frame(leg_ids, leg_types, currencies, rate_types, indices) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "leg_id": leg_ids,
            "trade_id": ["t1", "t1"],
            "leg_type": leg_types,
            "currency": currencies,
            "rate_type": rate_types,
            "reference_index": indices,
        }
    )


def test_interest_rate_swap_maps_to_tasa_codes() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "SWAP", "Bank")]),
        legs=_two_leg_frame(
            ["l_fix", "l_flt"],
            ["ACTIVE", "PASSIVE"],
            ["CLP", "CLP"],
            ["FIXED", "FLOAT"],
            [None, "ICP"],
        ),
        cashflows=_cashflows(
            [
                ("cf1", "t1", "l_fix", date(2027, 1, 1), 1_000.0, 1_000.0),
                ("cf2", "t1", "l_flt", date(2027, 1, 1), 1_000.0, 1_000.0),
            ]
        ),
        fx_factors={"CLP": 1.0},
        reference_date=REF,
    )
    codes = dict(zip(s02["leg_id"].to_list(), s02["exposicion_code"].to_list(), strict=True))
    assert codes == {"l_fix": "18", "l_flt": "31"}
    assert set(s02["instrument_kind"].to_list()) == {"SWAP_TASA"}


def test_cross_currency_forward_maps_to_moneda_codes() -> None:
    s02 = build_r07_s02(
        trades=_trades([("t1", "FORWARD", "Foreign Bank")]),
        legs=_two_leg_frame(
            ["l_usd", "l_clp"],
            ["ACTIVE", "PASSIVE"],
            ["USD", "CLP"],
            ["FIXED", "FIXED"],
            [None, None],
        ),
        cashflows=_cashflows(
            [
                ("cf1", "t1", "l_usd", date(2027, 1, 1), 1_000.0, 1_000.0),
                ("cf2", "t1", "l_clp", date(2027, 1, 1), 950_000.0, 950_000.0),
            ]
        ),
        fx_factors={"CLP": 1.0, "USD": 950.0},
        reference_date=REF,
    )
    codes = dict(zip(s02["leg_id"].to_list(), s02["exposicion_code"].to_list(), strict=True))
    assert codes == {"l_usd": "11", "l_clp": "24"}
    assert set(s02["instrument_kind"].to_list()) == {"FORWARD_MONEDA"}
