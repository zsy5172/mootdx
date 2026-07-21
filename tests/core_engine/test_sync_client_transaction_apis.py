from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import OutsideTradingSessionError
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import ConnectionLease
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport

ROOT = Path(__file__).resolve().parents[2]


def _transaction_body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "transaction" / case_id / "steps" / "01_transaction" / "response.body.bin").read_bytes()


def _transactions_body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "transactions" / case_id / "steps" / "01_transactions" / "response.body.bin").read_bytes()


def test_sync_client_transaction_decodes_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)
    transport = RecordingTransport(responses=[_transaction_body("live_sh_600036_last10")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.transaction(symbol="600036", start=0, offset=10)

    assert len(rows) == 10
    assert {"time", "price", "vol", "num", "buyorsell", "volume"} <= set(rows[0])


def test_sync_client_transactions_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_transactions_body("history_sh_600036_20170209_last10")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.transactions(symbol="600036", date="20170209", start=0, offset=10)

    assert len(rows) == 10
    assert {"time", "price", "vol", "buyorsell", "volume"} <= set(rows[0])
    assert "num" not in rows[0]


def test_sync_client_transaction_rejects_invalid_params(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SyncClient()
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)

    with pytest.raises(InvalidSymbolError):
        client.transaction(symbol="")
    with pytest.raises(ValueError):
        client.transaction(symbol="600036", start=-1)
    with pytest.raises(ValueError):
        client.transaction(symbol="600036", offset=0)
    with pytest.raises(ValueError, match="offset must be between 1 and 1800"):
        client.transaction(symbol="600036", offset=1801)
    with pytest.raises(OutsideTradingSessionError):
        monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: False)
        client.transaction(symbol="600036")


def test_sync_client_transactions_reject_invalid_params() -> None:
    client = SyncClient()

    with pytest.raises(InvalidSymbolError):
        client.transactions(symbol="", date="20170209")
    with pytest.raises(ValueError):
        client.transactions(symbol="600036", date="20170209", start=-1)
    with pytest.raises(ValueError):
        client.transactions(symbol="600036", date="20170209", offset=0)
    with pytest.raises(ValueError, match="offset must be between 1 and 2000"):
        client.transactions(symbol="600036", date="20170209", offset=2001)
    with pytest.raises(InvalidDateError):
        client.transactions(symbol="600036", date="2017/02/09")


def test_sync_client_transaction_rejects_bj_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SyncClient()
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)

    with pytest.raises(UnsupportedMarketError):
        client.transaction(symbol="430090")

    with pytest.raises(UnsupportedMarketError):
        client.transactions(symbol="430090", date="20200101")


def test_sync_client_transaction_propagates_scheduler_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server, select_error=NoHealthyServerError("no server"))
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(NoHealthyServerError):
        client.transaction(symbol="600036")


@dataclass
class SequenceScheduler:
    servers: list[ServerEndpoint]

    def __post_init__(self) -> None:
        self.success_calls: list[tuple[ServerEndpoint, object]] = []
        self.failure_calls: list[tuple[ServerEndpoint, Exception]] = []

    def select_server(
        self,
        context: RequestContext,
        excluded: set[tuple[str, int]] | None = None,
    ) -> ServerEndpoint:
        excluded = excluded or set()
        for server in self.servers:
            if (server.host, server.port) not in excluded:
                return server
        raise NoHealthyServerError(f"no server available for {context.api}")

    def record_success(self, server: ServerEndpoint, metrics: object) -> None:
        self.success_calls.append((server, metrics))

    def record_failure(self, server: ServerEndpoint, exc: Exception) -> None:
        self.failure_calls.append((server, exc))


class MultiTransportPool:
    def __init__(self, transports: dict[tuple[str, int], RecordingTransport]) -> None:
        self.transports = transports
        self.released: list[ConnectionLease] = []
        self.discarded: list[ConnectionLease] = []

    def acquire(self, server: ServerEndpoint) -> ConnectionLease:
        return ConnectionLease(
            server=server,
            transport=self.transports[(server.host, server.port)],
            created_at_ms=0.0,
            last_used_ms=0.0,
        )

    def release(self, lease: ConnectionLease) -> None:
        self.released.append(lease)

    def discard(self, lease: ConnectionLease) -> None:
        self.discarded.append(lease)

    def active_count(self, server: ServerEndpoint) -> int:
        return 0

    def close_all(self) -> None:
        for transport in self.transports.values():
            transport.close()


def test_sync_client_transaction_retries_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    transport_a = RecordingTransport(send_error=TransportTimeoutError("timed out"))
    transport_b = RecordingTransport(responses=[_transaction_body("live_sh_600036_last10")])
    pool = MultiTransportPool(
        {
            (server_a.host, server_a.port): transport_a,
            (server_b.host, server_b.port): transport_b,
        }
    )
    scheduler = SequenceScheduler([server_a, server_b])
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler, max_retries=1)

    rows = client.transaction(symbol="600036", offset=10)

    assert len(rows) == 10
    assert [lease.server for lease in pool.discarded] == [server_a]
    assert [lease.server for lease in pool.released] == [server_b]
    assert [call[0] for call in scheduler.failure_calls] == [server_a]
    assert [call[0] for call in scheduler.success_calls] == [server_b]


def test_sync_client_transaction_does_not_retry_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    transport_a = RecordingTransport(responses=[b"\x01\x00short"])
    transport_b = RecordingTransport(responses=[_transaction_body("live_sh_600036_last10")])
    pool = MultiTransportPool(
        {
            (server_a.host, server_a.port): transport_a,
            (server_b.host, server_b.port): transport_b,
        }
    )
    scheduler = SequenceScheduler([server_a, server_b])
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler, max_retries=1)

    with pytest.raises(ProtocolDecodeError):
        client.transaction(symbol="600036", offset=10)

    assert not pool.discarded
    assert [lease.server for lease in pool.released] == [server_a]
    assert not scheduler.failure_calls
    assert [call[0] for call in scheduler.success_calls] == [server_a]
    assert transport_b.sent_payloads == []


def test_sync_client_default_scheduler_wires_transaction_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mootdx_next.api.clients.is_trading_session", lambda: True)
    client = SyncClient()

    assert client.scheduler.select_server(RequestContext(api="transaction")) is not None
    assert client.scheduler.select_server(RequestContext(api="transactions")) is not None
