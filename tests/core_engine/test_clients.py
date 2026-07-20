import asyncio
import threading

import pytest

import mootdx_next.api.clients as clients_module
from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.models import ConnectionPoolSnapshot
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerHealthSnapshot
from mootdx_next.models import ServerEndpoint
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool


def test_sync_client_request_is_placeholder() -> None:
    client = SyncClient()
    with pytest.raises(NotImplementedError):
        client.request("stock_count", market=0)


class DummyAsyncSyncClient:
    def __init__(self) -> None:
        self.closed = False

    def stock_count(self, market: int) -> int:
        return market + 1

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False


def test_async_client_request_dispatches_to_sync_client() -> None:
    client = AsyncClient(sync_client=DummyAsyncSyncClient())
    assert asyncio.run(client.request("stock_count", market=0)) == 1


def test_async_client_request_rejects_unknown_api() -> None:
    client = AsyncClient(sync_client=DummyAsyncSyncClient())
    with pytest.raises(NotImplementedError):
        asyncio.run(client.request("unknown_api"))


def test_async_client_close_and_reconnect_toggle_state() -> None:
    client = AsyncClient(sync_client=DummyAsyncSyncClient())
    assert client.closed is False
    client.close()
    assert client.closed is True
    client.reconnect()
    assert client.closed is False


def test_async_client_resolves_sync_client_inside_each_worker_thread(monkeypatch) -> None:
    barrier = threading.Barrier(2)
    instances = []
    calls = []

    class ThreadBoundSyncClient:
        def __init__(self, **kwargs) -> None:
            self.created_on = threading.get_ident()
            instances.append(self)

        def stock_count(self, market: int) -> int:
            called_on = threading.get_ident()
            calls.append((id(self), self.created_on, called_on))
            barrier.wait(timeout=5)
            return market

    monkeypatch.setattr(clients_module, "SyncClient", ThreadBoundSyncClient)
    client = AsyncClient()

    async def concurrent_requests() -> list[int]:
        return await asyncio.gather(client.stock_count(0), client.stock_count(1))

    assert asyncio.run(concurrent_requests()) == [0, 1]
    assert len(instances) == 2
    assert len({client_id for client_id, _, _ in calls}) == 2
    assert all(created_on == called_on for _, created_on, called_on in calls)


def test_sync_client_close_and_reconnect_toggle_state() -> None:
    client = SyncClient()
    assert client.closed is False
    client.close()
    assert client.closed is True
    client.reconnect()
    assert client.closed is False


def test_connection_pool_is_instantiable() -> None:
    pool = ConnectionPool()
    assert pool.connections == {}
    snapshot = pool.snapshot()
    assert isinstance(snapshot, ConnectionPoolSnapshot)
    assert snapshot.total_active == 0
    assert snapshot.total_idle == 0


def test_server_pool_is_instantiable() -> None:
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="local")
    pool = ServerPool([server])
    assert pool.servers == [server]
    snapshot = pool.snapshot()
    assert len(snapshot) == 1
    assert isinstance(snapshot[0], ServerHealthSnapshot)
    assert snapshot[0].server == server
    assert pool.select(RequestContext(api="stock_count")) == server
