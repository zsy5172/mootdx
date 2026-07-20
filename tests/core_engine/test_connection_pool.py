from __future__ import annotations

import pytest

from mootdx_next.errors import PoolExhaustedError
from mootdx_next.models import ServerEndpoint
from mootdx_next.scheduler.pools import ConnectionPool


class DummyTransport:
    def __init__(self, name: str) -> None:
        self.name = name
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, ms: int) -> None:
        self.value += ms / 1000


def _transport_factory(created: list[DummyTransport]):
    def factory() -> DummyTransport:
        transport = DummyTransport(name=f"transport-{len(created)}")
        created.append(transport)
        return transport

    return factory


def test_acquire_creates_new_lease() -> None:
    created: list[DummyTransport] = []
    pool = ConnectionPool(transport_factory=_transport_factory(created))
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    lease = pool.acquire(server)

    assert lease.server == server
    assert lease.transport is created[0]
    snapshot = pool.snapshot()
    assert snapshot.total_active == 1
    assert snapshot.total_idle == 0
    assert snapshot.created_count == 1
    assert snapshot.reused_count == 0


def test_release_then_acquire_reuses_transport() -> None:
    created: list[DummyTransport] = []
    clock = Clock()
    pool = ConnectionPool(transport_factory=_transport_factory(created), time_fn=clock)
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    lease = pool.acquire(server)
    pool.release(lease)
    clock.advance_ms(25)
    reused = pool.acquire(server)

    assert reused is lease
    assert reused.last_used_ms == clock() * 1000
    snapshot = pool.snapshot()
    assert snapshot.created_count == 1
    assert snapshot.reused_count == 1
    assert snapshot.total_active == 1
    assert snapshot.total_idle == 0


def test_acquire_raises_when_pool_is_exhausted() -> None:
    created: list[DummyTransport] = []
    pool = ConnectionPool(
        transport_factory=_transport_factory(created),
        max_connections_per_server=1,
    )
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    pool.acquire(server)

    with pytest.raises(PoolExhaustedError):
        pool.acquire(server)

    assert pool.snapshot().exhausted_count == 1


def test_discard_closes_transport_and_removes_lease() -> None:
    created: list[DummyTransport] = []
    pool = ConnectionPool(transport_factory=_transport_factory(created))
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    lease = pool.acquire(server)
    transport = lease.transport
    pool.discard(lease)

    assert transport.close_count == 1
    assert pool.snapshot().total_active == 0
    assert pool.snapshot().total_idle == 0


def test_close_all_clears_active_and_idle_leases() -> None:
    created: list[DummyTransport] = []
    pool = ConnectionPool(transport_factory=_transport_factory(created))
    server = ServerEndpoint(host="127.0.0.1", port=7709)

    active = pool.acquire(server)
    idle = pool.acquire(server)
    pool.release(idle)
    pool.close_all()

    assert active.transport.close_count == 1
    assert idle.transport.close_count == 1
    snapshot = pool.snapshot()
    assert snapshot.total_active == 0
    assert snapshot.total_idle == 0

