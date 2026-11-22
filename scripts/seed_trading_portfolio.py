"""Seed a diverse TRADING portfolio and value it with the pricing engine.

Inserta trades/legs en ``dim_trades`` / ``dim_legs`` y los valoriza con
``expand_cashflows`` + ``compute_pv_dv01`` (curvas por moneda y tasas flotantes),
persistiendo ``fct_valued_cashflows``. Es idempotente: primero elimina los flujos
de los trades sembrados y luego los reinserta.

Uso::

    uv run python scripts/seed_trading_portfolio.py [--db data/market_risk.duckdb]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import cast

import polars as pl
from portfolio_pricing_engine.domain.bootstrapping import ZeroCurve
from portfolio_pricing_engine.domain.cashflow_engine import compute_pv_dv01, expand_cashflows
from risk_data_core import RiskDatabase

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _REPO_ROOT / "data" / "market_risk.duckdb"
_VALUATION_DATE = date(2026, 11, 5)

_TRADES: list[tuple[str, str, date, date, str]] = [
    ("corp-bond-5y", "BOND", date(2024, 7, 1), date(2029, 7, 1), "Empresa XYZ"),
    ("btu-10y", "BOND", date(2023, 7, 1), date(2033, 7, 1), "Treasury"),
    ("irs-clp-5y", "SWAP", date(2024, 7, 1), date(2029, 7, 1), "Banco Local"),
    ("clfclp-swap-3y", "SWAP", date(2024, 7, 1), date(2027, 7, 1), "Banco Local"),
    ("clfclp-fwd-1y", "FORWARD", date(2026, 7, 1), date(2027, 7, 1), "Banco Local"),
    ("usdclp-fwd-6m", "FORWARD", date(2026, 7, 1), date(2027, 1, 1), "Foreign Bank"),
    ("usdclp-ccs-7y", "SWAP", date(2024, 7, 1), date(2031, 7, 1), "Foreign Bank"),
]

# (leg_id, trade_id, leg_type, currency, notional, rate_type, coupon_rate,
#  payment_frequency, reference_index)
_LEGS: list[tuple[str, str, str, str, float, str, float, str, str | None]] = [
    (
        "corp-bond-5y-leg",
        "corp-bond-5y",
        "ACTIVE",
        "CLP",
        2_000_000_000.0,
        "FIXED",
        0.06,
        "ANNUAL",
        None,
    ),
    ("btu-10y-leg", "btu-10y", "ACTIVE", "UF", 500_000.0, "FIXED", 0.03, "ANNUAL", None),
    (
        "irs-clp-5y-fixed",
        "irs-clp-5y",
        "ACTIVE",
        "CLP",
        5_000_000_000.0,
        "FIXED",
        0.045,
        "SEMIANNUAL",
        None,
    ),
    (
        "irs-clp-5y-float",
        "irs-clp-5y",
        "PASSIVE",
        "CLP",
        5_000_000_000.0,
        "FLOAT",
        0.0,
        "SEMIANNUAL",
        "ICP",
    ),
    (
        "clfclp-swap-3y-clp",
        "clfclp-swap-3y",
        "ACTIVE",
        "CLP",
        1_000_000_000.0,
        "FIXED",
        0.05,
        "SEMIANNUAL",
        None,
    ),
    (
        "clfclp-swap-3y-uf",
        "clfclp-swap-3y",
        "PASSIVE",
        "UF",
        25_000.0,
        "FLOAT",
        0.0,
        "SEMIANNUAL",
        "UF",
    ),
    ("clfclp-fwd-1y-uf", "clfclp-fwd-1y", "ACTIVE", "UF", 10_000.0, "FIXED", 0.0, "ANNUAL", None),
    (
        "clfclp-fwd-1y-clp",
        "clfclp-fwd-1y",
        "PASSIVE",
        "CLP",
        400_000_000.0,
        "FIXED",
        0.0,
        "ANNUAL",
        None,
    ),
    (
        "usdclp-fwd-6m-usd",
        "usdclp-fwd-6m",
        "ACTIVE",
        "USD",
        5_000_000.0,
        "FIXED",
        0.0,
        "ANNUAL",
        None,
    ),
    (
        "usdclp-fwd-6m-clp",
        "usdclp-fwd-6m",
        "PASSIVE",
        "CLP",
        4_900_000_000.0,
        "FIXED",
        0.0,
        "ANNUAL",
        None,
    ),
    (
        "usdclp-ccs-7y-usd",
        "usdclp-ccs-7y",
        "ACTIVE",
        "USD",
        10_000_000.0,
        "FLOAT",
        0.0,
        "SEMIANNUAL",
        "SOFR",
    ),
    (
        "usdclp-ccs-7y-clp",
        "usdclp-ccs-7y",
        "PASSIVE",
        "CLP",
        9_700_000_000.0,
        "FLOAT",
        0.0,
        "SEMIANNUAL",
        "ICP",
    ),
]


def _trades_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_id": [trade[0] for trade in _TRADES],
            "instrument_type": [trade[1] for trade in _TRADES],
            "book": ["TRADING"] * len(_TRADES),
            "trade_date": [trade[2] for trade in _TRADES],
            "maturity_date": [trade[3] for trade in _TRADES],
            "counterparty": [trade[4] for trade in _TRADES],
        }
    )


def _legs_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "leg_id": [leg[0] for leg in _LEGS],
            "trade_id": [leg[1] for leg in _LEGS],
            "leg_type": [leg[2] for leg in _LEGS],
            "currency": [leg[3] for leg in _LEGS],
            "notional": [leg[4] for leg in _LEGS],
            "rate_type": [leg[5] for leg in _LEGS],
            "day_count_convention": ["ACT/365"] * len(_LEGS),
            "compounding_convention": ["CONTINUOUS"] * len(_LEGS),
            "coupon_rate": [leg[6] for leg in _LEGS],
            "payment_frequency": [leg[7] for leg in _LEGS],
            "reference_index": [leg[8] for leg in _LEGS],
        }
    )


def _load_curve(db: RiskDatabase, curve_name: str, valuation_date: date) -> ZeroCurve:
    frame = db.query_polars("SELECT * FROM fct_market_curves")
    nodes = frame.filter(
        (pl.col("curve_name") == curve_name) & (pl.col("valuation_date") <= valuation_date)
    )
    if nodes.is_empty():
        msg = f"no nodes for curve {curve_name!r} <= {valuation_date}"
        raise ValueError(msg)
    latest = nodes["valuation_date"].max()
    points = (
        nodes.filter(pl.col("valuation_date") == latest)
        .select(["tenor_yf", "zero_rate"])
        .sort("tenor_yf")
    )
    return ZeroCurve.from_zero_rates(points, cast(date, latest))


def _latest_factors(db: RiskDatabase, valuation_date: date) -> dict[str, float]:
    frame = db.query_polars("SELECT * FROM fct_market_factors")
    latest = (
        frame.filter(pl.col("valuation_date") <= valuation_date)
        .sort("valuation_date", descending=True)
        .unique(subset=["factor_code"], keep="first")
    )
    return {
        row["factor_code"]: float(row["value"])
        for row in latest.select(["factor_code", "value"]).iter_rows(named=True)
    }


def _latest_fx(db: RiskDatabase, valuation_date: date) -> dict[str, float]:
    frame = db.query_polars("SELECT * FROM fct_fx_rates")
    latest = (
        frame.filter(pl.col("valuation_date") <= valuation_date)
        .sort("valuation_date", descending=True)
        .unique(subset=["currency_pair"], keep="first")
    )
    return {
        row["currency_pair"]: float(row["spot_rate"])
        for row in latest.select(["currency_pair", "spot_rate"]).iter_rows(named=True)
    }


def main(db_path: Path = _DEFAULT_DB) -> int:
    db = RiskDatabase(Path(db_path), read_only=False)
    try:
        db.init_schema()
        curves = {
            "CLP": _load_curve(db, "CLP_BCP", _VALUATION_DATE),
            "UF": _load_curve(db, "CLP_BCU", _VALUATION_DATE),
            "USD": _load_curve(db, "USD_TREASURY", _VALUATION_DATE),
        }
        factors = _latest_factors(db, _VALUATION_DATE)
        fx = _latest_fx(db, _VALUATION_DATE)

        tib = factors.get("TIB_1D", 4.5) / 100.0
        floating_rates = {
            "SOFR": factors.get("SOFR", 3.92) / 100.0,
            "TIB": tib,
            "TPM": factors.get("TPM", 4.5) / 100.0,
            "ICP": tib,
            "UF": 0.02,
        }
        conversion_factors = {
            "CLP": 1.0,
            "UF": factors["UF"],
            "USD": fx["USD/CLP"],
        }

        trades = _trades_frame()
        legs = _legs_frame()
        cashflows = expand_cashflows(trades, legs, _VALUATION_DATE, floating_rates=floating_rates)
        valued = compute_pv_dv01(cashflows, curves=curves, conversion_factors=conversion_factors)

        trade_ids = trades["trade_id"].to_list()
        placeholders = ", ".join("?" for _ in trade_ids)
        db.execute(
            f"DELETE FROM fct_valued_cashflows WHERE trade_id IN ({placeholders})",
            trade_ids,
        )
        db.bulk_insert_polars(trades, "dim_trades", validate=False)
        db.bulk_insert_polars(legs, "dim_legs", validate=False)
        db.bulk_insert_polars(valued, "fct_valued_cashflows", validate=False)

        summary = (
            valued.group_by("trade_id")
            .agg(
                pl.len().alias("cashflows"),
                pl.col("present_value_clp").sum().alias("pv_clp"),
            )
            .sort("trade_id")
        )
        print(
            f"Seeded {trades.height} TRADING trades / "
            f"{legs.height} legs / {valued.height} cashflows."
        )
        print(f"Valuation date: {_VALUATION_DATE}")
        print(summary)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed TRADING portfolio for R07 validation.")
    parser.add_argument("--db", default=str(_DEFAULT_DB), help="Path to the DuckDB database.")
    args = parser.parse_args()
    raise SystemExit(main(Path(args.db)))
