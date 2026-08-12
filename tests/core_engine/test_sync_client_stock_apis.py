from __future__ import annotations

import struct
from dataclasses import dataclass

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.bse import BseRegistry
from mootdx_next.bse import BseSecurity
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import ConnectionLease
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.protocol import StdQuoteProtocol


def _build_stock_list_body(rows: list[dict[str, object]]) -> bytes:
    body = bytearray(struct.pack("<H", len(rows)))
    for row in rows:
        body.extend(
            struct.pack(
                "<6sH8s4sBI4s",
                str(row["code"]).encode("utf-8"),
                int(row.get("volunit", 100)),
                str(row.get("name", "")).encode("gbk", errors="ignore").ljust(8, b"\x00")[:8],
                b"\x00\x00\x00\x00",
                int(row.get("decimal_point", 2)),
                int(row.get("pre_close_raw", 0)),
                b"\x00\x00\x00\x00",
            )
        )
    return bytes(body)


class RecordingTransport:
    def __init__(self, responses: list[bytes] | None = None, send_error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.send_error = send_error
        self.metrics = TransportMetrics(last_latency_ms=1.5)
        self.sent_payloads: list[bytes] = []
        self.contexts: list[RequestContext] = []
        self.closed = False

    def connect(self, server: ServerEndpoint, timeout_ms: int | None = None) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return True

    def send(self, context: RequestContext, payload: bytes, server: ServerEndpoint) -> ResponseEnvelope:
        self.contexts.append(context)
        self.sent_payloads.append(payload)
        if self.send_error is not None:
            raise self.send_error
        if not self.responses:
            raise AssertionError("no response queued")
        return ResponseEnvelope(body=self.responses.pop(0), server=server, elapsed_ms=1.5)


@dataclass
class RecordingScheduler:
    server: ServerEndpoint
    select_error: Exception | None = None

    def __post_init__(self) -> None:
        self.success_calls: list[tuple[ServerEndpoint, TransportMetrics]] = []
        self.failure_calls: list[tuple[ServerEndpoint, Exception]] = []

    def select_server(
        self,
        context: RequestContext,
        excluded: set[tuple[str, int]] | None = None,
    ) -> ServerEndpoint:
        if self.select_error is not None:
            raise self.select_error
        return self.server

    def record_success(self, server: ServerEndpoint, metrics: TransportMetrics) -> None:
        self.success_calls.append((server, metrics))

    def record_failure(self, server: ServerEndpoint, exc: Exception) -> None:
        self.failure_calls.append((server, exc))


class RecordingConnectionPool:
    def __init__(self, transport: RecordingTransport) -> None:
        self.transport = transport
        self.released: list[ConnectionLease] = []
        self.discarded: list[ConnectionLease] = []
        self.server = ServerEndpoint(host="127.0.0.1", port=7709, label="test")

    def acquire(self, server: ServerEndpoint) -> ConnectionLease:
        self.server = server
        return ConnectionLease(server=server, transport=self.transport, created_at_ms=0.0, last_used_ms=0.0)

    def release(self, lease: ConnectionLease) -> None:
        self.released.append(lease)

    def discard(self, lease: ConnectionLease) -> None:
        self.discarded.append(lease)

    def active_count(self, server: ServerEndpoint) -> int:
        return 0

    def close_all(self) -> None:
        self.transport.close()


def test_sync_client_stock_count_returns_decoded_value() -> None:
    transport = RecordingTransport(responses=[struct.pack("<H", 321)])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.stock_count(1) == 321
    assert len(pool.released) == 1
    assert not pool.discarded
    assert scheduler.success_calls[0][0] == pool.server


def test_sync_client_applies_client_timeout_to_default_context() -> None:
    transport = RecordingTransport(responses=[struct.pack("<H", 321)])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(
        protocol=StdQuoteProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        timeout_ms=1234,
    )

    client.stock_count(1)

    assert transport.contexts[0].timeout_ms == 1234


@pytest.mark.parametrize("count, expected_page_starts", [(999, [0]), (1000, [0]), (1001, [0, 1000])])
def test_sync_client_stocks_pages_requests_by_thousands(count: int, expected_page_starts: list[int]) -> None:
    responses = [struct.pack("<H", count)] + [struct.pack("<H", 0) for _ in expected_page_starts]
    transport = RecordingTransport(responses=responses)
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.stocks(1) == []

    sent_page_starts = []
    for payload in transport.sent_payloads[1:]:
        market, start = struct.unpack("<HH", payload[-4:])
        assert market == 1
        sent_page_starts.append(start)
    assert sent_page_starts == expected_page_starts


def test_sync_client_stocks_aggregates_multiple_pages() -> None:
    page_one = _build_stock_list_body(
        [
            {"code": "600000", "name": "PFBANK", "pre_close_raw": 123456789},
            {"code": "600004", "name": "BAYPORT", "pre_close_raw": 98765432},
        ]
    )
    page_two = _build_stock_list_body(
        [
            {"code": "600006", "name": "CARGO", "pre_close_raw": 1234},
        ]
    )
    transport = RecordingTransport(responses=[struct.pack("<H", 1001), page_one, page_two])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.stocks(1)

    assert [row["code"] for row in rows] == ["600000", "600004", "600006"]
    assert len(pool.released) == 3
    assert not pool.discarded


def test_sync_client_stock_page_returns_one_normalized_page() -> None:
    page = _build_stock_list_body(
        [{"code": "600000", "name": "PFBANK", "pre_close_raw": 123456789}]
    )
    transport = RecordingTransport(responses=[page])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.stock_page(1, start=1000)

    assert rows[0]["market"] == 1
    assert rows[0]["source"] == "tdx"
    assert rows[0]["source_kind"] == "tdx_security_directory"
    assert struct.unpack("<HH", transport.sent_payloads[0][-4:]) == (1, 1000)


def test_sync_client_stocks_returns_empty_when_count_is_zero() -> None:
    transport = RecordingTransport(responses=[struct.pack("<H", 0)])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.stocks(1) == []
    assert len(transport.sent_payloads) == 1


def test_sync_client_rejects_invalid_market() -> None:
    client = SyncClient()

    with pytest.raises(UnsupportedMarketError):
        client.stock_count(9)

    with pytest.raises(UnsupportedMarketError):
        client.stock_page(9)

    with pytest.raises(ValueError):
        client.stock_page(1, start=-1)

    with pytest.raises(UnsupportedMarketError):
        client.stocks(9)


def test_quotes_all_preserves_every_symbol_across_native_batches() -> None:
    client = SyncClient()
    calls: list[list[str]] = []

    def quotes(symbol=None):
        page = list(symbol or [])
        calls.append(page)
        return [{"market": 1, "code": code[-6:]} for code in page]

    client.quotes = quotes  # type: ignore[method-assign]
    symbols = [f"sh{600000 + index:06d}" for index in range(161)]

    rows = client.quotes_all(symbols)

    assert [len(page) for page in calls] == [80, 80, 1]
    assert [row["code"] for row in rows] == [symbol[-6:] for symbol in symbols]
    assert client.quotes_all([]) == []


def test_quotes_rejects_more_than_one_native_packet() -> None:
    client = SyncClient()
    symbols = [f"sh{600000 + index:06d}" for index in range(81)]

    with pytest.raises(ProtocolDecodeError, match="at most 80.*quotes_all"):
        client.quotes(symbols)


def test_sync_client_bse_stocks_use_injected_registry_without_tdx_request() -> None:
    class Provider:
        def load(self):
            return [
                BseSecurity(
                    code="920786",
                    name="骑士乳业",
                    date="20260730",
                    pre_close=7.10,
                    open=7.12,
                    high=7.20,
                    low=7.00,
                    price=7.15,
                    volume=123400,
                    amount=880000.0,
                )
            ]

    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(
        protocol=StdQuoteProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        bse_registry=BseRegistry(Provider()),
    )

    assert client.stock_count(2) == 1
    assert client.stock_page(2, start=1) == []
    assert client.stocks(2) == [
        {
            "market": 2,
            "code": "920786",
            "name": "骑士乳业",
            "volunit": 100,
            "decimal_point": 2,
            "pre_close": 7.10,
            "date": "20260730",
            "open": 7.12,
            "high": 7.20,
            "low": 7.00,
            "price": 7.15,
            "volume": 123400,
            "amount": 880000.0,
            "source": "bse",
            "source_kind": "bse_market_snapshot",
        }
    ]
    assert transport.sent_payloads == []


def test_sync_client_propagates_scheduler_failure() -> None:
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server, select_error=NoHealthyServerError("no server"))
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(NoHealthyServerError):
        client.stock_count(1)

    assert not pool.released
    assert not pool.discarded


def test_pool_exhaustion_does_not_mark_server_as_unhealthy() -> None:
    class ExhaustedPool(RecordingConnectionPool):
        def acquire(self, server: ServerEndpoint) -> ConnectionLease:
            raise PoolExhaustedError("capacity")

    transport = RecordingTransport()
    pool = ExhaustedPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(
        protocol=StdQuoteProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        max_retries=0,
    )

    with pytest.raises(PoolExhaustedError):
        client.stock_count(1)

    assert scheduler.failure_calls == []


def test_sync_client_discards_lease_on_transport_failure() -> None:
    transport = RecordingTransport(send_error=TransportTimeoutError("timed out"))
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(TransportTimeoutError):
        client.stock_count(1)

    assert not pool.released
    assert len(pool.discarded) == 2
    assert len(scheduler.failure_calls) == 2


def test_sync_client_defaults_wire_up_protocol_and_pool() -> None:
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="local")
    client = SyncClient(servers=[server])

    assert isinstance(client.protocol, StdQuoteProtocol)
    assert client.scheduler.select_server(RequestContext(api="stock_count")) == server
