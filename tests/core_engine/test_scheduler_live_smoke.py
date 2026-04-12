from __future__ import annotations

import pytest

from mootdx_next.errors import EmptyResponseError
from mootdx_next.errors import InvalidResponseHeaderError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import PayloadDecompressionError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import TransportConnectionError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool
from tests.core_engine.support import PREFERRED_HQ_HOSTS
from tests.core_engine.support import build_stock_count_request


@pytest.mark.network
def test_scheduler_live_smoke() -> None:
    servers = [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]
    connection_pool = ConnectionPool(max_connections_per_server=2)
    server_pool = ServerPool(
        servers,
        connection_pool=connection_pool,
        failure_threshold=1,
        cooldown_ms=60_000,
    )
    context = RequestContext(api="stock_count", timeout_ms=1500)
    payload = build_stock_count_request()
    errors: list[str] = []

    for _ in range(len(servers)):
        try:
            server = server_pool.select(context)
        except NoHealthyServerError:
            break

        lease = connection_pool.acquire(server)
        try:
            envelope = lease.transport.send(context, payload, server)
            server_pool.mark_success(server, lease.transport.metrics)
            connection_pool.release(lease)

            assert envelope.header is not None
            assert envelope.body
            snapshot = server_pool.snapshot()
            current = next(item for item in snapshot if item.server == server)
            assert current.success_count >= 1
            assert connection_pool.snapshot().total_idle >= 1
            return
        except (
            EmptyResponseError,
            InvalidResponseHeaderError,
            PayloadDecompressionError,
            PoolExhaustedError,
            TransportConnectionError,
            TransportTimeoutError,
        ) as exc:
            server_pool.mark_failure(server, exc)
            connection_pool.discard(lease)
            errors.append(f"{server.label}({server.host}:{server.port}): {type(exc).__name__}: {exc}")

    connection_pool.close_all()
    pytest.skip("no reachable HQ host for scheduler smoke: " + "; ".join(errors))
