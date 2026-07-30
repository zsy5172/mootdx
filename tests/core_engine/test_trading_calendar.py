from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.trading_calendar import TradingCalendarRegistry


class CalendarClient(SyncClient):
    def __init__(self) -> None:
        super().__init__(trading_calendar_registry=TradingCalendarRegistry())
        self.bar_calls = 0
        self.transaction_calls: list[str] = []

    def index_bars_all(self, symbol, frequency=9, market=None, page_size=800, max_pages=None):
        self.bar_calls += 1
        return [
            {"datetime": "2026-07-17 15:00"},
            {"datetime": "2026-07-20 15:00"},
            {"datetime": "2026-07-21 15:00"},
        ]

    def transactions_day(self, symbol, date, page_size=2000, max_pages=None):
        self.transaction_calls.append(str(date))
        return [{"datetime": f"{date} 09:30", "price": 1.0}]


def test_client_calendar_filters_ranges_and_reuses_snapshot() -> None:
    client = CalendarClient()

    assert client.trading_days("20260717", "20260721") == (
        "20260717",
        "20260720",
        "20260721",
    )
    assert client.is_trading_day("2026-07-20")
    assert not client.is_trading_day("20260718")
    assert client.bar_calls == 1

    client.trading_days(refresh=True)
    assert client.bar_calls == 2


def test_iter_transactions_defaults_to_confirmed_exchange_days() -> None:
    client = CalendarClient()

    chunks = list(client.iter_transactions("600036", "20260718", "20260721"))

    assert [day for day, _ in chunks] == ["20260720", "20260721"]
    assert client.transaction_calls == ["20260720", "20260721"]


def test_iter_transactions_can_explicitly_restore_natural_days() -> None:
    client = CalendarClient()

    chunks = list(
        client.iter_transactions(
            "600036",
            "20260718",
            "20260720",
            trading_days_only=False,
        )
    )

    assert [day for day, _ in chunks] == ["20260718", "20260719", "20260720"]
    assert client.bar_calls == 0


def test_async_calendar_and_iteration_match_sync_client() -> None:
    sync_client = CalendarClient()
    client = AsyncClient(sync_client=sync_client)

    async def run():
        days = await client.trading_days("20260718", "20260721")
        is_day = await client.is_trading_day("20260720")
        chunks = [
            item
            async for item in client.iter_transactions(
                "600036",
                "20260718",
                "20260721",
            )
        ]
        return days, is_day, chunks

    days, is_day, chunks = asyncio.run(run())

    assert days == ("20260720", "20260721")
    assert is_day
    assert [day for day, _ in chunks] == list(days)


def test_trading_calendar_registry_coordinates_concurrent_refresh() -> None:
    registry = TradingCalendarRegistry()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return ["20260721", "20260720", "20260720"]

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(registry.get, loader) for _ in range(3)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert snapshots[0] == ("20260720", "20260721")
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_trading_days_rejects_partial_or_reverse_ranges() -> None:
    client = CalendarClient()

    with pytest.raises(ValueError, match="provided together"):
        client.trading_days("20260720")
    with pytest.raises(ValueError, match="end_date"):
        client.trading_days("20260721", "20260720")
