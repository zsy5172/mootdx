from __future__ import annotations

import pytest

from mootdx_next.errors import NoHealthyServerError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool


class DummyTransport:
    def close(self) -> None:
        return None


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, ms: int) -> None:
        self.value += ms / 1000


def _pool(clock: Clock | None = None) -> ConnectionPool:
    return ConnectionPool(transport_factory=DummyTransport, time_fn=clock or Clock())


def test_select_prefers_server_with_fewer_active_connections() -> None:
    clock = Clock()
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    connection_pool = _pool(clock)
    connection_pool.acquire(server_a)
    pool = ServerPool([server_a, server_b], connection_pool=connection_pool, time_fn=clock)

    selected = pool.select(RequestContext(api="stock_count"))

    assert selected == server_b


def test_select_prefers_lower_latency_when_active_counts_match() -> None:
    clock = Clock()
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    connection_pool = _pool(clock)
    pool = ServerPool([server_a, server_b], connection_pool=connection_pool, time_fn=clock)

    pool.mark_success(server_a, TransportMetrics(last_latency_ms=200.0))
    pool.mark_success(server_b, TransportMetrics(last_latency_ms=50.0))

    assert pool.select(RequestContext(api="stock_count")) == server_b


def test_mark_failure_enters_cooldown_after_threshold() -> None:
    clock = Clock()
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    pool = ServerPool([server], connection_pool=_pool(clock), time_fn=clock)

    pool.mark_failure(server, RuntimeError("boom-1"))
    first = pool.snapshot()[0]
    assert first.state == "healthy"
    assert first.consecutive_failures == 1

    pool.mark_failure(server, RuntimeError("boom-2"))
    second = pool.snapshot()[0]
    assert second.state == "cooldown"
    assert second.cooldown_until_ms == clock() * 1000 + 30_000


def test_server_is_selectable_again_after_cooldown() -> None:
    clock = Clock()
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    pool = ServerPool([server], connection_pool=_pool(clock), cooldown_ms=5000, time_fn=clock)

    pool.mark_failure(server, RuntimeError("boom-1"))
    pool.mark_failure(server, RuntimeError("boom-2"))

    with pytest.raises(NoHealthyServerError):
        pool.select(RequestContext(api="stock_count"))

    clock.advance_ms(5001)
    assert pool.select(RequestContext(api="stock_count")) == server


def test_select_raises_when_all_servers_are_in_cooldown() -> None:
    clock = Clock()
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    pool = ServerPool([server_a, server_b], connection_pool=_pool(clock), time_fn=clock)

    for server in (server_a, server_b):
        pool.mark_failure(server, RuntimeError("boom-1"))
        pool.mark_failure(server, RuntimeError("boom-2"))

    with pytest.raises(NoHealthyServerError):
        pool.select(RequestContext(api="stock_count"))


def test_snapshot_includes_current_active_connection_count() -> None:
    clock = Clock()
    server = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    connection_pool = _pool(clock)
    connection_pool.acquire(server)
    pool = ServerPool([server], connection_pool=connection_pool, time_fn=clock)

    snapshot = pool.snapshot()[0]

    assert snapshot.active_connections == 1
    assert snapshot.state == "healthy"


def test_select_skips_excluded_servers() -> None:
    clock = Clock()
    server_a = ServerEndpoint(host="127.0.0.1", port=7709, label="a")
    server_b = ServerEndpoint(host="127.0.0.2", port=7709, label="b")
    pool = ServerPool([server_a, server_b], connection_pool=_pool(clock), time_fn=clock)

    selected = pool.select(RequestContext(api="stock_count"), excluded={(server_a.host, server_a.port)})

    assert selected == server_b
