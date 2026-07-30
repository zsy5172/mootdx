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
