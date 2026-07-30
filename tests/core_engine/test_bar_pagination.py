from __future__ import annotations

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import ProtocolDecodeError


def _row(day: int, close: float, previous_close: float | None) -> dict[str, object]:
    return {
        "datetime": f"2026-07-{day:02d} 15:00",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "vol": 1.0,
        "volume": 1.0,
        "amount": close,
        "previous_close": previous_close,
    }


class PagedClient(SyncClient):
    def __init__(self, *, repeated: bool = False) -> None:
        super().__init__()
        self.repeated = repeated
        self.bar_calls: list[tuple[str, int | str, int, int]] = []
        self.index_calls: list[tuple[str, int | str, int, int, int | None]] = []

    def _page(self, start: int) -> list[dict[str, object]]:
        pages = {
            0: [_row(3, 3.0, 2.0), _row(4, 4.0, 3.0)],
            2: [_row(1, 1.0, None), _row(2, 2.0, 1.0)],
            4: [],
        }
        if self.repeated and start == 2:
            return pages[0]
        return pages[start]

    def bars(self, symbol, frequency=9, start=0, offset=800):
        self.bar_calls.append((symbol, frequency, start, offset))
        return self._page(start)

    def index_bars(self, symbol, frequency=9, start=0, offset=800, market=None):
        self.index_calls.append((symbol, frequency, start, offset, market))
        return self._page(start)


def test_bars_all_prepends_pages_and_repairs_previous_close_boundaries() -> None:
    client = PagedClient()

    rows = client.bars_all("600036", frequency="day", page_size=2)

    assert [row["close"] for row in rows] == [1.0, 2.0, 3.0, 4.0]
    assert [row["previous_close"] for row in rows] == [None, 1.0, 2.0, 3.0]
    assert client.bar_calls == [
        ("600036", 9, 0, 2),
        ("600036", 9, 2, 2),
        ("600036", 9, 4, 2),
    ]


def test_bars_until_matches_from_newest_to_oldest_like_upstream() -> None:
    client = PagedClient()

    rows = client.bars_until(
        "600036",
        lambda row: str(row["datetime"]) <= "2026-07-02 15:00",
        frequency=9,
        page_size=2,
    )

    assert [row["close"] for row in rows] == [2.0, 3.0, 4.0]
    assert [row["previous_close"] for row in rows] == [1.0, 2.0, 3.0]
    assert [call[2] for call in client.bar_calls] == [0, 2]


def test_index_bars_all_preserves_explicit_market() -> None:
    client = PagedClient()

    rows = client.index_bars_all("sh000001", frequency="week", market=1, page_size=2)

    assert len(rows) == 4
    assert all(call[-1] == 1 for call in client.index_calls)
    assert all(call[1] == 5 for call in client.index_calls)


def test_bar_pagination_respects_explicit_page_cap() -> None:
    client = PagedClient()

    rows = client.bars_all("600036", page_size=2, max_pages=1)

    assert [row["close"] for row in rows] == [3.0, 4.0]
    assert client.bar_calls == [("600036", 9, 0, 2)]


def test_bar_pagination_rejects_non_progressing_server_pages() -> None:
    client = PagedClient(repeated=True)

    with pytest.raises(ProtocolDecodeError, match="did not advance"):
        client.bars_all("600036", page_size=2)


@pytest.mark.parametrize(
    ("predicate", "page_size", "max_pages", "error"),
    [
        (None, 2, None, TypeError),
        (lambda _row: False, 0, None, ValueError),
        (lambda _row: False, 801, None, ValueError),
        (lambda _row: False, 2, 0, ValueError),
    ],
)
def test_bar_pagination_validates_controls(predicate, page_size, max_pages, error) -> None:
    client = PagedClient()

    with pytest.raises(error):
        client.bars_until(
            "600036",
            predicate,
            page_size=page_size,
            max_pages=max_pages,
        )
