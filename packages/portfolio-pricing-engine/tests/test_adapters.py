from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pandera.errors import SchemaError
from portfolio_pricing_engine.adapters.alpaca_adapter import AlpacaAdapter
from portfolio_pricing_engine.adapters.bcch_adapter import BcchAdapter
from portfolio_pricing_engine.adapters.digital_gob_holiday_adapter import (
    DigitalGobHolidayAdapter,
)
from portfolio_pricing_engine.adapters.duckdb_trade_repository import DuckDBTradeRepository
from portfolio_pricing_engine.adapters.fred_adapter import FredAdapter
from risk_data_core import RiskDatabase
from risk_data_core.models import Leg, Trade

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _bcch_client(values: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.params["timeseries"]
        value = values[code]
        return httpx.Response(
            200,
            json={
                "Codigo": 0,
                "Series": {
                    "Obs": [
                        {
                            "indexDateString": "02-01-2026",
                            "value": str(value),
                            "statusCode": "OK",
                        }
                    ]
                },
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bcch_fetch_par_rates_returns_decimal_rates() -> None:
    adapter = BcchAdapter(
        token="test",
        client=_bcch_client(_load("bcch_values.json")),
        min_interval=0.0,
    )
    frame = adapter.fetch_par_rates(date(2026, 1, 2), "CLP_BCP")

    assert frame.height == 3
    assert frame["tenor_yf"].to_list() == pytest.approx([2.0, 5.0, 10.0])
    assert frame["par_rate"].to_list() == pytest.approx([0.05, 0.055, 0.06])


def test_bcch_fetch_fx_validates_and_returns_spot_rate() -> None:
    adapter = BcchAdapter(
        token="test",
        client=_bcch_client(_load("bcch_values.json")),
        min_interval=0.0,
    )
    frame = adapter.fetch_fx(date(2026, 1, 2), "USD/CLP")

    assert frame["spot_rate"].to_list() == pytest.approx([900.25])


def test_bcch_fx_rejects_non_positive_rate() -> None:
    values = _load("bcch_values.json") | {"F073.TCO.PRE.Z.D": 0.0}
    adapter = BcchAdapter(
        token="test",
        client=_bcch_client(values),
        min_interval=0.0,
    )

    with pytest.raises(SchemaError):
        adapter.fetch_fx(date(2026, 1, 2), "USD/CLP")


def test_fred_fetch_par_rates() -> None:
    values = _load("fred_values.json")

    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["series_id"]
        return httpx.Response(
            200,
            json={"observations": [{"date": "2026-01-02", "value": str(values[series_id])}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FredAdapter(api_key="test", client=client)
    frame = adapter.fetch_par_rates(date(2026, 1, 2), "USD_TREASURY")

    assert frame.height == 3
    assert frame["par_rate"].to_list() == pytest.approx([0.0425, 0.041, 0.04])


def test_alpaca_fetch_price() -> None:
    bars = _load("alpaca_bars.json")

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbols"]
        return httpx.Response(200, json={"bars": {symbol: bars[symbol]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = AlpacaAdapter(api_key="k", api_secret="s", client=client)
    frame = adapter.fetch_price(date(2026, 1, 2), "AAPL")

    assert frame["price"].to_list() == pytest.approx([151.5])
    assert frame["currency"].to_list() == ["USD"]


def test_duckdb_trade_repository_loads_active_trades_and_legs() -> None:
    db = RiskDatabase(Path(":memory:"), read_only=False)
    try:
        db.init_schema()
        with db.session_factory() as session:
            session.add(
                Trade(
                    trade_id="t1",
                    instrument_type="BOND",
                    book="BANKING",
                    trade_date=date(2025, 1, 2),
                    maturity_date=date(2027, 1, 2),
                )
            )
            session.commit()
        with db.session_factory() as session:
            session.add(
                Leg(
                    leg_id="l1",
                    trade_id="t1",
                    leg_type="ACTIVE",
                    currency="CLP",
                    notional=100.0,
                    rate_type="FIXED",
                    day_count_convention="ACT/365",
                    compounding_convention="CONTINUOUS",
                    coupon_rate=0.05,
                    payment_frequency="ANNUAL",
                )
            )
            session.commit()

        repository = DuckDBTradeRepository(db)
        trades = repository.load_trades(date(2025, 1, 2))
        legs = repository.load_legs(date(2025, 1, 2))

        assert trades["trade_id"].to_list() == ["t1"]
        assert legs["notional"].to_list() == [100.0]
    finally:
        db.close()


def test_bcch_decodes_iso_8859_1_payload() -> None:
    values = _load("bcch_values.json")

    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.params["timeseries"]
        body = json.dumps(
            {
                "Codigo": 0,
                "Descripcion": "éxito",
                "Series": {"Obs": [{"indexDateString": "02-01-2026", "value": str(values[code])}]},
            },
            ensure_ascii=False,
        ).encode("iso-8859-1")
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json; charset=ISO-8859-1"},
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.fetch_par_rates(date(2026, 1, 2), "CLP_BCP")

    assert frame.height == 3


def test_bcch_requests_a_lookback_window() -> None:
    captured: dict[str, str] = {}
    values = _load("bcch_values.json")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["firstdate"] = request.url.params["firstdate"]
        captured["lastdate"] = request.url.params["lastdate"]
        code = request.url.params["timeseries"]
        return httpx.Response(
            200,
            json={"Series": {"Obs": [{"value": str(values[code])}]}},
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    adapter.fetch_fx(date(2026, 1, 2), "USD/CLP")

    assert captured["firstdate"] < captured["lastdate"]


def test_fred_requests_a_lookback_window() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["start"] = request.url.params["observation_start"]
        captured["end"] = request.url.params["observation_end"]
        return httpx.Response(200, json={"observations": [{"value": "4.0"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FredAdapter(api_key="test", client=client)
    adapter.fetch_par_rates(date(2026, 1, 2), "USD_TREASURY")

    assert captured["start"] < captured["end"]


def test_digital_gob_holiday_adapter_fetches_year() -> None:
    payload = _load("digital_gob_feriados.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = DigitalGobHolidayAdapter(client=client)
    frame = adapter.fetch_year(2026)

    assert frame.height == 2
    assert frame["date"].to_list() == [date(2026, 1, 1), date(2026, 4, 3)]
    assert frame["name"].to_list() == ["Año Nuevo", "Viernes Santo"]


def test_bcch_fetch_series_history_returns_dated_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Series": {
                    "Obs": [
                        {"indexDateString": "02-01-2026", "value": "900.25", "statusCode": "OK"},
                        {"indexDateString": "03-01-2026", "value": "910.0", "statusCode": "OK"},
                    ]
                }
            },
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.fetch_series_history("F073.TCO.PRE.Z.D", date(2026, 1, 1), date(2026, 1, 5))

    assert frame["as_of_date"].to_list() == [date(2026, 1, 2), date(2026, 1, 3)]
    assert frame["value"].to_list() == pytest.approx([900.25, 910.0])


def test_bcch_fetch_series_history_skips_non_ok_observations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Series": {
                    "Obs": [
                        {"indexDateString": "02-01-2026", "value": "900.25", "statusCode": "OK"},
                        {"indexDateString": "03-01-2026", "value": "0", "statusCode": "ND"},
                    ]
                }
            },
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.fetch_series_history("F073.TCO.PRE.Z.D", date(2026, 1, 1), date(2026, 1, 5))

    assert frame.height == 1
    assert frame["as_of_date"].to_list() == [date(2026, 1, 2)]


def test_bcch_fetch_curve_history_normalizes_percent_to_decimal() -> None:
    values = {"BCP2": 5.0, "BCP5": 5.5, "BCP10": 6.0}

    def handler(request: httpx.Request) -> httpx.Response:
        code = request.url.params["timeseries"]
        return httpx.Response(
            200,
            json={
                "Series": {
                    "Obs": [
                        {
                            "indexDateString": "02-01-2026",
                            "value": str(values[code]),
                            "statusCode": "OK",
                        }
                    ]
                }
            },
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.fetch_curve_history(
        {"2Y": "BCP2", "5Y": "BCP5", "10Y": "BCP10"},
        date(2026, 1, 1),
        date(2026, 1, 5),
    )

    assert frame.height == 3
    assert frame["par_rate"].to_list() == pytest.approx([0.05, 0.055, 0.06])


def test_fred_fetch_series_history_skips_missing_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-01-02", "value": "18.5"},
                    {"date": "2026-01-03", "value": "."},
                    {"date": "2026-01-05", "value": "19.0"},
                ]
            },
        )

    adapter = FredAdapter(
        api_key="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    frame = adapter.fetch_series_history("VIXCLS", date(2026, 1, 1), date(2026, 1, 10))

    assert frame["as_of_date"].to_list() == [date(2026, 1, 2), date(2026, 1, 5)]
    assert frame["value"].to_list() == pytest.approx([18.5, 19.0])


def test_fred_fetch_curve_history_normalizes_percent_to_decimal() -> None:
    values = {"DGS2": 4.25, "DGS5": 4.1, "DGS10": 4.0, "DGS30": 4.3}

    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["series_id"]
        return httpx.Response(
            200,
            json={"observations": [{"date": "2026-01-02", "value": str(values[series_id])}]},
        )

    adapter = FredAdapter(
        api_key="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    frame = adapter.fetch_curve_history(
        {"2Y": "DGS2", "5Y": "DGS5", "10Y": "DGS10", "30Y": "DGS30"},
        date(2026, 1, 1),
        date(2026, 1, 5),
    )

    assert frame.height == 4
    assert frame["par_rate"].to_list() == pytest.approx([0.0425, 0.041, 0.04, 0.043])


def test_bcch_search_series_returns_catalog_frame() -> None:
    payload = {
        "Codigo": 0,
        "SeriesInfos": [
            {
                "seriesId": "F022.TPM.TIN.D001.NO.Z.D",
                "frequencyCode": "DAILY",
                "spanishTitle": "TPM",
                "englishTitle": "MPR",
                "firstObservation": "07-02-1997",
                "lastObservation": "13-10-2025",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["function"] == "SearchSeries"
        return httpx.Response(200, json=payload)

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.search_series("DAILY")

    assert frame.height == 1
    assert frame["series_id"].to_list() == ["F022.TPM.TIN.D001.NO.Z.D"]
    assert frame["frequency"].to_list() == ["DAILY"]


def test_bcch_fetch_series_history_skips_nan_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Series": {
                    "Obs": [
                        {"indexDateString": "02-01-2026", "value": "900.25", "statusCode": "OK"},
                        {"indexDateString": "03-01-2026", "value": "NaN", "statusCode": "OK"},
                    ]
                }
            },
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
    )
    frame = adapter.fetch_series_history("X", date(2026, 1, 1), date(2026, 1, 5))

    assert frame["as_of_date"].to_list() == [date(2026, 1, 2)]


def test_bcch_retries_transient_http_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"Codigo": -1})
        return httpx.Response(
            200,
            json={
                "Series": {
                    "Obs": [
                        {"indexDateString": "02-01-2026", "value": "900.25", "statusCode": "OK"}
                    ]
                }
            },
        )

    adapter = BcchAdapter(
        token="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0.0,
        max_attempts=2,
    )
    frame = adapter.fetch_series_history("X", date(2026, 1, 1), date(2026, 1, 5))

    assert calls["n"] == 2
    assert frame["value"].to_list() == pytest.approx([900.25])
