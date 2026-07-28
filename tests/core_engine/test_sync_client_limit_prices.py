from __future__ import annotations

import struct

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.limits import invalidate_price_limit_cache
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


def _limit_body(
    rows: list[tuple[int, int, float, float]],
) -> bytes:
    return struct.pack("<H", len(rows)) + b"".join(
        struct.pack("<BIff", market, code, limit_up, limit_down) for market, code, limit_up, limit_down in rows
    )


@pytest.fixture(autouse=True)
def clear_process_price_limit_cache():
    invalidate_price_limit_cache()
    yield
    invalidate_price_limit_cache()


def test_sync_client_limit_prices_sends_page_and_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_limit_body([(1, 600053, 7.5, 6.14)])])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.limit_prices(start=0, count=1)

    assert rows == [{"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14}]
    assert struct.unpack("<8H", transport.sent_payloads[0][10:]) == (
        0x0452,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
    )


@pytest.mark.parametrize(
    ("start", "count"),
    [(-1, 1), (65536, 1), (0, 0), (0, 2001)],
)
def test_sync_client_limit_prices_rejects_invalid_window(start: int, count: int) -> None:
    client = SyncClient()
    with pytest.raises(ValueError):
        client.limit_prices(start=start, count=count)


def test_price_limit_prefers_server_special_price_without_quote_request() -> None:
    transport = RecordingTransport(responses=[_limit_body([(1, 600053, 7.5, 6.14)])])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    result = client.price_limit("sh600053")

    assert result == {
        "market": 1,
        "code": "600053",
        "limit_up": 7.5,
        "limit_down": 6.14,
        "source": "server",
    }
    assert len(transport.sent_payloads) == 1


def test_price_limit_calculates_ordinary_a_share_from_previous_close() -> None:
    client = SyncClient()
    limit_calls = 0
    quote_calls = 0

    def limit_prices(start=0, count=2000):
        nonlocal limit_calls
        limit_calls += 1
        return []

    def quotes(symbol=None):
        nonlocal quote_calls
        quote_calls += 1
        return [{"market": 1, "code": "600036", "last_close": 39.0}]

    client.limit_prices = limit_prices
    client.quotes = quotes

    first = client.price_limit("600036")
    second = client.price_limit("sh600036")

    assert (
        first
        == second
        == {
            "market": 1,
            "code": "600036",
            "limit_up": 42.9,
            "limit_down": 35.1,
            "source": "calculated",
        }
    )
    assert limit_calls == 1
    assert quote_calls == 2


def test_price_limit_refresh_reloads_special_table() -> None:
    client = SyncClient()
    loads = []

    def limit_prices(start=0, count=2000):
        loads.append(len(loads))
        price = 7.5 if len(loads) == 1 else 7.6
        return [{"market": 1, "code": "600053", "limit_up": price, "limit_down": 6.14}]

    client.limit_prices = limit_prices
    client.quotes = lambda symbol=None: pytest.fail("special price must not request quotes")

    assert client.price_limit("600053")["limit_up"] == 7.5
    assert client.price_limit("600053")["limit_up"] == 7.5
    assert client.price_limit("600053", refresh=True)["limit_up"] == 7.6
    assert len(loads) == 2


def test_price_limit_returns_none_when_no_quote_is_available() -> None:
    client = SyncClient()
    client.limit_prices = lambda start=0, count=2000: []
    client.quotes = lambda symbol=None: []

    assert client.price_limit("600036") is None
