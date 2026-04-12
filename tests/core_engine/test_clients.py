import asyncio

import pytest

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


def test_async_client_request_is_placeholder() -> None:
    client = AsyncClient()
    with pytest.raises(NotImplementedError):
        asyncio.run(client.request("stock_count", market=0))


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
