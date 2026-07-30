from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass

import pytest

import mootdx_next.api.ex_clients as ex_clients_module
from mootdx_next.api.ex_clients import AsyncExClient
from mootdx_next.api.ex_clients import ExSyncClient
from mootdx_next.ex_markets import ExMarketRegistry
from mootdx_next.models import ResponseEnvelope
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


EX_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "request",
    "markets",
    "instrument_count",
    "instrument",
    "instruments",
    "quote",
    "quotes",
    "bars",
    "minute",
    "minutes",
    "transaction",
    "transactions",
    "bars_range",
}


def _public_api(owner: type) -> set[str]:
    return {
        name
        for name, value in owner.__dict__.items()
        if not name.startswith("_") and (inspect.isroutine(value) or isinstance(value, property))
    }


def test_sync_and_async_ex_clients_have_exact_api_parity() -> None:
    assert _public_api(ExSyncClient) == EX_PUBLIC_API
    assert _public_api(AsyncExClient) == EX_PUBLIC_API


class ExMatrixProtocol:
    def __init__(self) -> None:
        self.encode_calls: list[tuple[str, dict[str, object]]] = []
        self.decode_calls: list[tuple[str, dict[str, object]]] = []

    def encode(self, api: str, **kwargs: object) -> bytes:
        self.encode_calls.append((api, kwargs))
        return api.encode("ascii")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        self.decode_calls.append((api, kwargs))
        if api == "instrument_count":
            return 2001
        if api == "instruments":
            start = int(kwargs["start"])
            count = int(kwargs["count"])
            return [{"start": start + index} for index in range(count)]
        if api == "quote":
            return {"market": kwargs["market"], "code": kwargs["code"]}
        if api == "markets":
            return [{"market": 31, "category": 2, "name": "香港主板", "short_name": "KH"}]
        return [{"api": api}]


def _client() -> tuple[ExSyncClient, ExMatrixProtocol]:
    protocol = ExMatrixProtocol()
    transport = RecordingTransport(responses=[b""] * 20)
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    return (
        ExSyncClient(
            protocol=protocol,
            connection_pool=pool,
            scheduler=scheduler,
            market_registry=ExMarketRegistry(),
        ),
        protocol,
    )


@dataclass(frozen=True)
class ExCallCase:
    method: str
    kwargs: dict[str, object]
    api: str
    encoded: dict[str, object]


EX_CALL_CASES = (
    ExCallCase("markets", {}, "markets", {}),
    ExCallCase("instrument_count", {}, "instrument_count", {}),
    ExCallCase(
        "instrument",
        {"start": 20, "offset": 15},
        "instruments",
        {"start": 20, "count": 15},
    ),
    ExCallCase(
        "quote",
        {"market": 31, "symbol": "00700", "market_category": 2},
        "quote",
        {"market": 31, "code": "00700", "market_category": 2},
    ),
    ExCallCase(
        "quotes",
        {"market": 31, "category": 2, "start": 20, "offset": 15},
        "quotes",
        {"market": 31, "category": 2, "start": 20, "count": 15},
    ),
    ExCallCase(
        "bars",
        {
            "market": 31,
            "symbol": "00700",
            "frequency": "day",
            "start": 20,
            "offset": 15,
            "market_category": 2,
        },
        "bars",
        {
            "category": 9,
            "market": 31,
            "code": "00700",
            "start": 20,
            "count": 15,
            "market_category": 2,
        },
    ),
    ExCallCase(
        "minute",
        {"market": 31, "symbol": "00700", "market_category": 2},
        "minute",
        {"market": 31, "code": "00700", "market_category": 2},
    ),
    ExCallCase(
        "minutes",
        {
            "market": 31,
            "symbol": "00700",
            "date": "2026-07-29",
            "market_category": 2,
        },
        "minutes",
        {"market": 31, "code": "00700", "date": 20260729, "market_category": 2},
    ),
    ExCallCase(
        "transaction",
        {"market": 31, "symbol": "00700", "start": 20, "offset": 15},
        "transaction",
        {"market": 31, "code": "00700", "start": 20, "count": 15},
    ),
    ExCallCase(
        "transactions",
        {
            "market": 31,
            "symbol": "00700",
            "date": 20260729,
            "start": 20,
            "offset": 15,
        },
        "transactions",
        {"market": 31, "code": "00700", "date": 20260729, "start": 20, "count": 15},
    ),
    ExCallCase(
        "bars_range",
        {
            "market": 31,
            "symbol": "00700",
            "start": "2026-01-01",
            "end": 20260729,
            "market_category": 2,
        },
        "bars_range",
        {
            "market": 31,
            "code": "00700",
            "start_date": 20260101,
            "end_date": 20260729,
            "market_category": 2,
        },
    ),
)


@pytest.mark.parametrize("case", EX_CALL_CASES, ids=lambda case: case.method)
def test_sync_ex_client_dispatches_every_protocol_api(case: ExCallCase) -> None:
    client, protocol = _client()

    getattr(client, case.method)(**case.kwargs)

    assert protocol.encode_calls == [(case.api, case.encoded)]
    assert protocol.decode_calls == [(case.api, case.encoded)]


def test_instruments_paginates_to_reported_total() -> None:
    client, protocol = _client()

    rows = client.instruments(page_size=1000)

    assert len(rows) == 2001
    assert rows[-1]["start"] == 2000
    assert [call[1] for call in protocol.encode_calls if call[0] == "instruments"] == [
        {"start": 0, "count": 1000},
        {"start": 1000, "count": 1000},
        {"start": 2000, "count": 1},
    ]


def test_bars_resolves_market_category_from_cached_market_directory() -> None:
    client, protocol = _client()

    rows = client.bars(31, "00700", market_category=None)

    assert rows
    assert [api for api, _ in protocol.encode_calls] == ["markets", "bars"]
    assert protocol.decode_calls[-1][1]["market_category"] == 2


@pytest.mark.parametrize(
    ("method", "kwargs", "message"),
    [
        ("instrument", {"offset": 1001}, "between 1 and 1000"),
        ("quotes", {"market": 31, "category": 2, "offset": 101}, "between 1 and 100"),
        ("quotes", {"market": 31, "category": 1}, "2 \(HK\) or 3"),
        ("bars", {"market": 31, "symbol": "00700", "offset": 701}, "between 1 and 700"),
        ("transaction", {"market": 31, "symbol": "00700", "offset": 1801}, "between 1 and 1800"),
        (
            "transactions",
            {"market": 31, "symbol": "00700", "date": 20260729, "offset": 1801},
            "between 1 and 1800",
        ),
        (
            "bars_range",
            {"market": 31, "symbol": "00700", "start": 20260730, "end": 20260729},
            "on or before",
        ),
    ],
)
def test_ex_client_rejects_values_the_server_would_silently_truncate(
    method: str,
    kwargs: dict[str, object],
    message: str,
) -> None:
    client, _ = _client()
    with pytest.raises(ValueError, match=message):
        getattr(client, method)(**kwargs)


class AsyncRecorder:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def call(*args: object, **kwargs: object):
            self.calls.append((name, args, kwargs))
            if name == "instrument_count":
                return 1
            if name == "quote":
                return {"code": "00700"}
            return []

        return call

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("markets", ()),
        ("instrument_count", ()),
        ("instrument", (20, 15)),
        ("instruments", (1000,)),
        ("quote", (31, "00700")),
        ("quotes", (31, 2, 20, 15)),
        ("bars", (31, "00700", 9, 20, 15)),
        ("minute", (31, "00700")),
        ("minutes", (31, "00700", 20260729)),
        ("transaction", (31, "00700", 20, 15)),
        ("transactions", (31, "00700", 20260729, 20, 15)),
        ("bars_range", (31, "00700", 20260101, 20260729)),
    ],
)
def test_async_ex_client_dispatches_to_sync_peer(method: str, args: tuple[object, ...]) -> None:
    recorder = AsyncRecorder()
    client = AsyncExClient(sync_client=recorder)

    asyncio.run(getattr(client, method)(*args))

    assert recorder.calls == [(method, args, {})]


def test_async_ex_client_resolves_client_inside_each_worker_thread(monkeypatch) -> None:
    barrier = threading.Barrier(2)
    instances = []
    calls = []

    class ThreadBoundExClient:
        def __init__(self, **kwargs) -> None:
            self.created_on = threading.get_ident()
            instances.append(self)

        def instrument_count(self) -> int:
            called_on = threading.get_ident()
            calls.append((id(self), self.created_on, called_on))
            barrier.wait(timeout=5)
            return 1

    monkeypatch.setattr(ex_clients_module, "ExSyncClient", ThreadBoundExClient)
    client = AsyncExClient()

    async def concurrent_requests() -> list[int]:
        return await asyncio.gather(client.instrument_count(), client.instrument_count())

    assert asyncio.run(concurrent_requests()) == [1, 1]
    assert len(instances) == 2
    assert len({client_id for client_id, _, _ in calls}) == 2
    assert all(created_on == called_on for _, created_on, called_on in calls)


def test_async_ex_close_closes_every_worker_local_client(monkeypatch) -> None:
    barrier = threading.Barrier(2)
    instances = []

    class ClosableThreadClient:
        def __init__(self, **kwargs) -> None:
            self.closed = False
            instances.append(self)

        def instrument_count(self) -> int:
            barrier.wait(timeout=5)
            return 1

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(ex_clients_module, "ExSyncClient", ClosableThreadClient)
    client = AsyncExClient()

    async def concurrent_requests() -> list[int]:
        return await asyncio.gather(client.instrument_count(), client.instrument_count())

    assert asyncio.run(concurrent_requests()) == [1, 1]
    assert len(instances) == 2
    client.close()
    assert client.closed
    assert all(item.closed for item in instances)
