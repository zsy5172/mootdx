from __future__ import annotations

import asyncio

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.trading_calendar import TradingCalendarRegistry


def _row(day: int, minute: int, price: float) -> dict[str, object]:
    time = f"09:{minute:02d}"
    return {
        "time": time,
        "datetime": f"2026-07-{day:02d} {time}",
        "price": price,
        "vol": 1,
        "volume": 1,
    }


class PagedTransactionClient(SyncClient):
    def __init__(self, *, repeated: bool = False) -> None:
        registry = TradingCalendarRegistry()
        registry.get(lambda: ["20260720", "20260721", "20260730"])
        super().__init__(trading_calendar_registry=registry)
        self.repeated = repeated
        self.live_calls: list[tuple[str, int, int]] = []
        self.history_calls: list[tuple[str, str, int, int]] = []

    def _page(self, start: int, day: int = 30) -> list[dict[str, object]]:
        pages = {
            0: [_row(day, 33, 3.0), _row(day, 34, 4.0)],
            2: [_row(day, 31, 1.0), _row(day, 32, 2.0)],
            4: [],
        }
        if self.repeated and start == 2:
            return pages[0]
        return pages[start]

    def transaction(self, symbol, start=0, offset=800):
        self.live_calls.append((symbol, start, offset))
        return self._page(start)

    def transactions(self, symbol, date, start=0, offset=800):
        normalized_date = str(date).replace("-", "")
        self.history_calls.append((symbol, normalized_date, start, offset))
        if normalized_date in {"20260718", "20260719"}:
            return []
        return self._page(start, day=int(normalized_date[-2:]))


class TransactionHistoryClient(SyncClient):
    def __init__(self, *, monthly=True) -> None:
        super().__init__()
        self.monthly = monthly
        self.calls: list[tuple[object, ...]] = []

    def bars_all(self, symbol, frequency=9, page_size=800, max_pages=None):
        self.calls.append(("bars_all", symbol, frequency, page_size, max_pages))
        if not self.monthly:
            return []
        return [
            {"datetime": "2026-08-31 15:00"},
            {"datetime": "2026-07-31 15:00"},
        ]

    def trading_days(self, start_date=None, end_date=None, refresh=False):
        self.calls.append(("trading_days", start_date, end_date, refresh))
        return tuple(
            date
            for date in ("20260720", "20260721", "20260729", "20260730")
            if str(start_date) <= date <= str(end_date)
        )

    def transactions_day(self, symbol, date, page_size=2000, max_pages=None):
        self.calls.append(("transactions_day", symbol, date, page_size, max_pages))
        return [_row(int(str(date)[-2:]), 31, 10.0)]

    def transaction_all(self, symbol, page_size=1800, max_pages=None):
        self.calls.append(("transaction_all", symbol, page_size, max_pages))
        return [{"time": "09:31", "price": 11.0, "vol": 1, "volume": 1}]


def test_transaction_all_prepends_pages_in_chronological_order() -> None:
    client = PagedTransactionClient()

    rows = client.transaction_all("600036", page_size=2)

    assert [row["price"] for row in rows] == [1.0, 2.0, 3.0, 4.0]
    assert client.live_calls == [
        ("600036", 0, 2),
        ("600036", 2, 2),
        ("600036", 4, 2),
    ]


def test_transactions_day_normalizes_date_and_honors_page_cap() -> None:
    client = PagedTransactionClient()

    rows = client.transactions_day("600036", "2026-07-30", page_size=2, max_pages=1)

    assert [row["price"] for row in rows] == [3.0, 4.0]
    assert client.history_calls == [("600036", "20260730", 0, 2)]


def test_iter_transactions_yields_lazy_daily_chunks_and_can_include_empty_days() -> None:
    client = PagedTransactionClient()

    non_empty = list(
        client.iter_transactions(
            "600036",
            "20260718",
            "20260720",
            page_size=2,
        )
    )
    with_empty = list(
        client.iter_transactions(
            "600036",
            "20260718",
            "20260720",
            include_empty=True,
            page_size=2,
            max_pages=1,
        )
    )

    assert [date for date, _ in non_empty] == ["20260720"]
    assert [date for date, _ in with_empty] == ["20260720"]

    natural_days = list(
        client.iter_transactions(
            "600036",
            "20260718",
            "20260720",
            include_empty=True,
            trading_days_only=False,
            page_size=2,
            max_pages=1,
        )
    )
    assert [date for date, _ in natural_days] == ["20260718", "20260719", "20260720"]
    assert natural_days[0][1] == []


def test_async_iter_transactions_has_sync_chunk_semantics() -> None:
    sync_client = PagedTransactionClient()
    client = AsyncClient(sync_client=sync_client)

    async def collect():
        return [
            item
            async for item in client.iter_transactions(
                "600036",
                "20260720",
                "20260720",
                page_size=2,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks[0][0] == "20260720"
    assert [row["price"] for row in chunks[0][1]] == [1.0, 2.0, 3.0, 4.0]


def test_transaction_pagination_rejects_non_progressing_pages() -> None:
    client = PagedTransactionClient(repeated=True)

    with pytest.raises(ProtocolDecodeError, match="did not advance"):
        client.transaction_all("600036", page_size=2)


@pytest.mark.parametrize(
    ("page_size", "max_pages", "error"),
    [
        (0, None, ValueError),
        (1801, None, ValueError),
        (2, 0, ValueError),
    ],
)
def test_transaction_pagination_validates_controls(page_size, max_pages, error) -> None:
    client = PagedTransactionClient()

    with pytest.raises(error):
        client.transaction_all("600036", page_size=page_size, max_pages=max_pages)


def test_iter_transactions_rejects_reverse_date_range() -> None:
    client = PagedTransactionClient()

    with pytest.raises(ValueError, match="end_date"):
        list(client.iter_transactions("600036", "20260721", "20260720"))


def test_transaction_history_is_lazy_and_infers_first_month() -> None:
    client = TransactionHistoryClient()

    iterator = client.iter_transaction_history(
        "600036",
        before="20260721",
        page_size=1000,
        max_pages=2,
    )

    assert client.calls == []
    first = next(iterator)
    assert first[0] == "20260720"
    assert client.calls == [
        ("bars_all", "600036", 6, 800, None),
        ("trading_days", "20260701", "20260721", False),
        ("transactions_day", "600036", "20260720", 1000, 2),
    ]


def test_transaction_history_returns_empty_when_monthly_bars_are_empty() -> None:
    client = TransactionHistoryClient(monthly=False)

    assert list(client.iter_transaction_history("600036", before="20260721")) == []
    assert client.calls == [("bars_all", "600036", 6, 800, None)]


def test_transaction_history_caps_history_at_yesterday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.today_yyyymmdd", lambda: "20260730")
    client = TransactionHistoryClient()

    chunks = list(client.iter_transaction_history("600036", before="20260730"))

    assert [date for date, _ in chunks] == ["20260720", "20260721", "20260729"]
    assert ("trading_days", "20260701", "20260729", False) in client.calls
    assert not any(call[0] == "transaction_all" for call in client.calls)


def test_transaction_history_can_explicitly_include_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.today_yyyymmdd", lambda: "20260730")
    client = TransactionHistoryClient()

    chunks = list(
        client.iter_transaction_history(
            "600036",
            before="20260730",
            include_today=True,
            page_size=2000,
            max_pages=1,
        )
    )

    assert chunks[-1][0] == "20260730"
    assert ("transaction_all", "600036", 1800, 1) in client.calls


def test_transaction_history_stops_before_listing_month() -> None:
    client = TransactionHistoryClient()

    assert list(client.iter_transaction_history("600036", before="20260630")) == []
    assert not any(call[0] == "trading_days" for call in client.calls)


def test_async_transaction_history_has_sync_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.today_yyyymmdd", lambda: "20260730")
    sync_client = TransactionHistoryClient()
    client = AsyncClient(sync_client=sync_client)

    async def collect():
        return [
            item
            async for item in client.iter_transaction_history(
                "600036",
                before="20260720",
                max_pages=1,
            )
        ]

    chunks = asyncio.run(collect())

    assert [date for date, _ in chunks] == ["20260720"]
    assert ("bars_all", "600036", 6, 800, None) in sync_client.calls
