from __future__ import annotations

from mootdx_next.api.clients import SyncClient
from mootdx_next.models import ConnectionLease
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.scheduler.pools import ServerPool


class _SemanticProtocol:
    def encode(self, api: str, **kwargs: object) -> bytes:
        return api.encode("ascii")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object):
        if api in {"bars", "index_bars"}:
            return [] if envelope.body == b"empty" else [{"datetime": "2026-08-20"}]
        raise NotImplementedError(api)


class _RoutedTransport:
    def __init__(self) -> None:
        self.metrics = TransportMetrics(last_latency_ms=1.0)
        self.hosts: list[str] = []

    def send(
        self,
        context: RequestContext,
        payload: bytes,
        server: ServerEndpoint,
    ) -> ResponseEnvelope:
        self.hosts.append(server.host)
        body = b"empty" if server.host == "empty" else b"data"
        return ResponseEnvelope(body=body, server=server)

    def close(self) -> None:
        return None


class _RoutedPool:
    def __init__(self, transport: _RoutedTransport) -> None:
        self.transport = transport

    def acquire(self, server: ServerEndpoint) -> ConnectionLease:
        return ConnectionLease(
            server=server,
            transport=self.transport,
            created_at_ms=0.0,
            last_used_ms=0.0,
        )

    def release(self, lease: ConnectionLease) -> None:
        return None

    def discard(self, lease: ConnectionLease) -> None:
        return None

    def active_count(self, server: ServerEndpoint) -> int:
        return 0

    def close_all(self) -> None:
        return None


def test_empty_bars_retry_another_server_without_marking_transport_failure() -> None:
    transport = _RoutedTransport()
    pool = _RoutedPool(transport)
    servers = [
        ServerEndpoint(host="empty", port=7709),
        ServerEndpoint(host="data", port=7709),
    ]
    scheduler = ServerPool(servers=servers, connection_pool=pool)
    client = SyncClient(
        protocol=_SemanticProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        max_retries=1,
    )

    assert client.bars("sh600036", offset=10) == [{"datetime": "2026-08-20"}]
    assert transport.hosts == ["empty", "data"]
    snapshots = scheduler.snapshot()
    assert all(item.failure_count == 0 for item in snapshots)
