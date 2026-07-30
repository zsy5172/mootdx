from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from mootdx_next.adapters import xdxr_by_date_to_frame
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import TransportTimeoutError


def _equity_event(
    date: str,
    *,
    category: int,
    float_shares: float,
    total_shares: float,
) -> dict[str, object]:
    timestamp = datetime.strptime(date, "%Y-%m-%d")
    return {
        "market": 1,
        "code": "600036",
        "symbol": "sh600036",
        "datetime": f"{date} 15:00",
        "year": timestamp.year,
        "month": timestamp.month,
        "day": timestamp.day,
        "category": category,
        "name": "股本变化",
        "panhouliutong_shares": float_shares,
        "houzongguben_shares": total_shares,
    }


class XdxrClient(SyncClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.failures: dict[str, int] = {}
        self.rows = [
            _equity_event("2020-01-02", category=2, float_shares=1000, total_shares=2000),
            {
                "year": 2023,
                "month": 6,
                "day": 1,
                "category": 1,
                "fenhong": 1.0,
            },
            {
                "year": 2023,
                "month": 6,
                "day": 1,
                "category": 11,
                "suogu": 0.5,
            },
            _equity_event("2024-01-02", category=5, float_shares=1500, total_shares=2500),
        ]

    def xdxr(self, symbol: str):
        self.calls.append(symbol)
        failures = self.failures.get(symbol, 0)
        if failures:
            self.failures[symbol] = failures - 1
            raise TransportTimeoutError("retry me")
        return list(self.rows)

    def stock_codes(self, refresh: bool = False):
        return ["sh600036", "sz000001"]


def test_equity_at_uses_latest_effective_equity_event_by_calendar_day() -> None:
    client = XdxrClient()

    assert client.equity_at("600036", "20191231") is None
    old = client.equity_at("600036", "2020-01-02")
    current = client.equity_at("600036", datetime(2026, 7, 30, 9, 0))

    assert old is not None and old["float_shares"] == 1000
    assert current is not None
    assert current["date"] == "2024-01-02"
    assert current["float_shares"] == 1500
    assert current["total_shares"] == 2500


def test_turnover_accepts_explicit_share_and_lot_units() -> None:
    client = XdxrClient()

    assert client.turnover("600036", "20260730", 150, volume_unit="shares") == pytest.approx(10)
    assert client.turnover("600036", "20260730", 1.5, volume_unit="lots") == pytest.approx(10)
    assert client.turnover("600036", "20100101", 100) is None


def test_xdxr_by_date_preserves_multiple_same_day_events_and_filters() -> None:
    client = XdxrClient()

    grouped = client.xdxr_by_date("600036")
    filtered = client.xdxr_by_date("600036", categories=(1,))

    assert list(grouped) == ["2020-01-02", "2023-06-01", "2024-01-02"]
    assert [row["category"] for row in grouped["2023-06-01"]] == [1, 11]
    assert [row["category"] for row in filtered["2023-06-01"]] == [1]
    frame = xdxr_by_date_to_frame(grouped)
    assert frame.index.name == "event_date"
    assert list(frame.loc["2023-06-01", "category"]) == [1, 11]
    assert isinstance(xdxr_by_date_to_frame({}).index, pd.DatetimeIndex)


def test_market_value_uses_share_units_and_yuan_price() -> None:
    client = XdxrClient()

    result = client.market_value("600036", "20260730", 39.5)

    assert result is not None
    assert result["float_shares"] == 1500
    assert result["total_shares"] == 2500
    assert result["float_market_value"] == pytest.approx(59250)
    assert result["total_market_value"] == pytest.approx(98750)
    assert client.market_value("600036", "20100101", 39.5) is None


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), True])
def test_market_value_rejects_invalid_prices(price) -> None:
    with pytest.raises(ValueError):
        XdxrClient().market_value("600036", "20260730", price)


@pytest.mark.parametrize("categories", [(True,), (-1,), (256,)])
def test_xdxr_by_date_rejects_invalid_categories(categories) -> None:
    with pytest.raises(ValueError):
        XdxrClient().xdxr_by_date("600036", categories)


@pytest.mark.parametrize(
    ("volume", "unit"),
    [
        (-1, "shares"),
        (float("inf"), "shares"),
        (True, "shares"),
        (1, "unknown"),
    ],
)
def test_turnover_rejects_ambiguous_or_invalid_volume(volume, unit) -> None:
    client = XdxrClient()

    with pytest.raises(ValueError):
        client.turnover("600036", "20260730", volume, volume_unit=unit)


def test_iter_xdxr_is_lazy_and_retries_transport_failures() -> None:
    client = XdxrClient()
    client.failures["sh600036"] = 1

    iterator = client.iter_xdxr(["600036", "sz000001"], retries=1)
    assert client.calls == []
    first = next(iterator)

    assert first[0] == "sh600036"
    assert client.calls == ["sh600036", "sh600036"]
    assert next(iterator)[0] == "sz000001"


def test_iter_xdxr_defaults_to_process_security_stock_codes() -> None:
    client = XdxrClient()

    symbols = [symbol for symbol, _ in client.iter_xdxr()]

    assert symbols == ["sh600036", "sz000001"]


def test_iter_xdxr_validates_symbols_and_retry_count() -> None:
    client = XdxrClient()

    with pytest.raises(ValueError):
        next(client.iter_xdxr(["600036"], retries=-1))
    with pytest.raises(InvalidSymbolError):
        next(client.iter_xdxr([""]))
