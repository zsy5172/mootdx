from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.api.pandas import AsyncPandasClient
from mootdx_next.api.pandas import PandasClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.minute_bars import rebuild_minute_bars_241


def _bar(day: str, time: str, *, volume: float, amount: float) -> dict[str, object]:
    year, month, date = (int(value) for value in day.split("-"))
    hour, minute = (int(value) for value in time.split(":"))
    return {
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.4,
        "vol": volume,
        "volume": volume,
        "amount": amount,
        "year": year,
        "month": month,
        "day": date,
        "hour": hour,
        "minute": minute,
        "datetime": f"{day} {time}",
        "previous_close": 9.8,
    }


def _trade(time: str, price: float, volume: int, num: int | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "time": time,
        "price": price,
        "vol": volume,
        "volume": volume,
        "amount": price * volume * 100,
    }
    if num is not None:
        row["num"] = num
    return row


def test_rebuild_241_aggregates_every_auction_record_and_conserves_turnover() -> None:
    bars = [
        _bar("2026-07-29", "09:31", volume=150, amount=151_000),
        _bar("2026-07-29", "09:32", volume=20, amount=20_800),
    ]
    original = deepcopy(bars)
    transactions = {
        "20260729": [
            _trade("09:25", 10.0, 30, 3),
            _trade("09:25", 10.1, 20, 2),
            _trade("09:30", 10.2, 100, 10),
        ]
    }

    rows = rebuild_minute_bars_241(bars, transactions)

    auction, regular = rows[:2]
    assert bars == original
    assert [row["datetime"] for row in rows] == [
        "2026-07-29 09:30",
        "2026-07-29 09:31",
        "2026-07-29 09:32",
    ]
    assert auction["is_call_auction"] is True
    assert (auction["open"], auction["high"], auction["low"], auction["close"]) == (
        10.0,
        10.1,
        10.0,
        10.1,
    )
    assert auction["vol"] == 50
    assert auction["amount"] == pytest.approx(50_200)
    assert auction["order_count"] == 5
    assert regular["open"] == 10.2
    assert regular["previous_close"] == 10.1
    assert regular["vol"] == 100
    assert regular["amount"] == pytest.approx(100_800)
    assert regular["order_count"] == 10
    assert auction["vol"] + regular["vol"] == original[0]["vol"]
    assert auction["amount"] + regular["amount"] == pytest.approx(original[0]["amount"])


def test_rebuild_241_preserves_a_day_when_transactions_have_no_auction() -> None:
    bars = [_bar("2026-07-29", "09:31", volume=100, amount=100_000)]

    rows = rebuild_minute_bars_241(bars, {"20260729": [_trade("09:30", 10.0, 100)]})

    assert len(rows) == 1
    assert rows[0]["datetime"] == "2026-07-29 09:31"
    assert rows[0]["is_call_auction"] is False
    assert rows[0]["auction_adjusted"] is False


def test_rebuild_241_rejects_inconsistent_bar_and_transaction_snapshots() -> None:
    bars = [_bar("2026-07-29", "09:31", volume=10, amount=10_000)]
    transactions = {"20260729": [_trade("09:25", 10.0, 11)]}

    with pytest.raises(ProtocolDecodeError, match="exceeds"):
        rebuild_minute_bars_241(bars, transactions)


class Minute241Client(SyncClient):
    def __init__(self) -> None:
        super().__init__()
        self.bar_calls: list[tuple[str, int, int, int]] = []
        self.live_transactions: list[tuple[str, int, int | None]] = []
        self.transaction_days: list[tuple[str, str, int, int | None]] = []

    def bars(self, symbol, frequency=9, start=0, offset=800):
        self.bar_calls.append((symbol, frequency, start, offset))
        return [_bar("2026-07-29", "09:31", volume=150, amount=151_000)]

    def bars_until(self, symbol, predicate, frequency=9, page_size=800, max_pages=None):
        self.bar_calls.append((symbol, frequency, 0, page_size))
        return [_bar("2026-07-29", "09:31", volume=150, amount=151_000)]

    def transactions_day(self, symbol, date, page_size=2000, max_pages=None):
        self.transaction_days.append((symbol, str(date), page_size, max_pages))
        return [
            _trade("09:25", 10.0, 50),
            _trade("09:30", 10.2, 100),
        ]

    def transaction_all(self, symbol, page_size=1800, max_pages=None):
        self.live_transactions.append((symbol, page_size, max_pages))
        return [
            _trade("09:25", 10.0, 50, 5),
            _trade("09:30", 10.2, 100, 10),
        ]


def test_client_241_uses_frequency_8_and_loads_each_historical_day_once() -> None:
    client = Minute241Client()

    rows = client.minute_bars_241("600036", start=5, offset=20, transaction_max_pages=3)

    assert [row["datetime"] for row in rows] == [
        "2026-07-29 09:30",
        "2026-07-29 09:31",
    ]
    assert client.bar_calls == [("600036", 8, 5, 20)]
    assert client.transaction_days == [("600036", "20260729", 2000, 3)]


def test_client_241_uses_live_transactions_for_the_current_trading_date(monkeypatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.today_yyyymmdd", lambda: "20260729")
    client = Minute241Client()

    rows = client.minute_bars_241("600036", transaction_max_pages=2)

    assert len(rows) == 2
    assert client.live_transactions == [("600036", 1800, 2)]
    assert client.transaction_days == []


def test_async_and_pandas_241_facades_match_the_sync_rows() -> None:
    sync_client = Minute241Client()
    async_client = AsyncClient(sync_client=sync_client)
    async_pandas = AsyncPandasClient(
        raw_client=AsyncClient(sync_client=Minute241Client())
    )

    async def run():
        rows = await async_client.minute_bars_241_all(
            "600036",
            page_size=20,
            max_pages=1,
        )
        frame = await async_pandas.minute_bars_241("600036", offset=20)
        return rows, frame

    async_rows, async_frame = asyncio.run(run())
    frame = PandasClient(raw_client=Minute241Client()).minute_bars_241("600036", offset=20)

    assert [row["datetime"] for row in async_rows] == [
        "2026-07-29 09:30",
        "2026-07-29 09:31",
    ]
    assert list(frame.index.strftime("%H:%M")) == ["09:30", "09:31"]
    assert list(async_frame.index.strftime("%H:%M")) == ["09:30", "09:31"]
    assert frame.loc["2026-07-29 09:30", "is_call_auction"]
