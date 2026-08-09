from __future__ import annotations

import time
from collections.abc import Callable

from mootdx_next.constants import BLOCK_FUND_HOSTS
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.models import RequestContext
from mootdx_next.models import ConnectionLease
from mootdx_next.models import ConnectionPoolSnapshot
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import ServerHealthSnapshot
from mootdx_next.models import TransportMetrics
from mootdx_next.transport.socket_transport import SyncSocketTransport


class ConnectionPool:
    def __init__(
        self,
        transport_factory: Callable[[], object] = SyncSocketTransport,
        max_connections_per_server: int = 2,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport_factory = transport_factory
        self.max_connections_per_server = max_connections_per_server
        self._time_fn = time_fn
        self.connections: dict[tuple[str, int], list[ConnectionLease]] = {}
        self._active: dict[tuple[str, int], list[ConnectionLease]] = {}
        self._created_count = 0
        self._reused_count = 0
        self._exhausted_count = 0

    def _server_key(self, server: ServerEndpoint) -> tuple[str, int]:
        return (server.host, server.port)

    def _now_ms(self) -> float:
        return self._time_fn() * 1000

    def acquire(self, server: ServerEndpoint) -> ConnectionLease:
        key = self._server_key(server)
        idle = self.connections.setdefault(key, [])
        active = self._active.setdefault(key, [])

        if idle:
            lease = idle.pop()
            lease.last_used_ms = self._now_ms()
            active.append(lease)
            self._reused_count += 1
            return lease

        if len(idle) + len(active) >= self.max_connections_per_server:
            self._exhausted_count += 1
            raise PoolExhaustedError(
                f"connection pool exhausted for {server.host}:{server.port}"
            )

        now_ms = self._now_ms()
        lease = ConnectionLease(
            server=server,
            transport=self._transport_factory(),
            created_at_ms=now_ms,
            last_used_ms=now_ms,
        )
        active.append(lease)
        self._created_count += 1
        return lease

    def release(self, connection: ConnectionLease) -> None:
        key = self._server_key(connection.server)
        active = self._active.setdefault(key, [])
        if connection in active:
            active.remove(connection)
            connection.last_used_ms = self._now_ms()
            self.connections.setdefault(key, []).append(connection)

    def discard(self, connection: ConnectionLease) -> None:
        key = self._server_key(connection.server)
        active = self._active.setdefault(key, [])
        idle = self.connections.setdefault(key, [])
        if connection in active:
            active.remove(connection)
        if connection in idle:
            idle.remove(connection)
        connection.transport.close()

    def active_count(self, server: ServerEndpoint) -> int:
        return len(self._active.get(self._server_key(server), []))

    def close_all(self) -> None:
        leases: list[ConnectionLease] = []
        for group in self.connections.values():
            leases.extend(group)
        for group in self._active.values():
            leases.extend(group)

        for lease in leases:
            lease.transport.close()

        self.connections.clear()
        self._active.clear()

    def snapshot(self) -> ConnectionPoolSnapshot:
        total_active = sum(len(group) for group in self._active.values())
        total_idle = sum(len(group) for group in self.connections.values())
        return ConnectionPoolSnapshot(
            total_active=total_active,
            total_idle=total_idle,
            created_count=self._created_count,
            reused_count=self._reused_count,
            exhausted_count=self._exhausted_count,
        )


class ServerPool:
    def __init__(
        self,
        servers: list[ServerEndpoint] | None = None,
        connection_pool: ConnectionPool | None = None,
        failure_threshold: int = 2,
        cooldown_ms: int = 30_000,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.servers = list(servers or [])
        self.connection_pool = connection_pool or ConnectionPool()
        self.failure_threshold = failure_threshold
        self.cooldown_ms = cooldown_ms
        self._time_fn = time_fn
        self._server_order = {
            (server.host, server.port): index for index, server in enumerate(self.servers)
        }
        self._health = {
            (server.host, server.port): ServerHealthSnapshot(server=server) for server in self.servers
        }

    def _server_key(self, server: ServerEndpoint) -> tuple[str, int]:
        return (server.host, server.port)

    def _now_ms(self) -> float:
        return self._time_fn() * 1000

    def _current_state(self, snapshot: ServerHealthSnapshot, now_ms: float) -> str:
        if snapshot.cooldown_until_ms > now_ms:
            return "cooldown"
        return "healthy"

    def select(
        self,
        context: RequestContext,
        excluded: set[tuple[str, int]] | None = None,
    ) -> ServerEndpoint:
        return self.select_server(context, excluded=excluded)

    def select_server(
        self,
        context: RequestContext,
        excluded: set[tuple[str, int]] | None = None,
    ) -> ServerEndpoint:
        now_ms = self._now_ms()
        candidates: list[ServerHealthSnapshot] = []
        excluded = excluded or set()

        for server in self.servers:
            if self._server_key(server) in excluded:
                continue
            snapshot = self._health[self._server_key(server)]
            snapshot.active_connections = self.connection_pool.active_count(server)
            snapshot.state = self._current_state(snapshot, now_ms)
            if snapshot.state == "healthy":
                candidates.append(snapshot)

        if not candidates:
            raise NoHealthyServerError(f"no healthy server available for {context.api}")

        if context.api == "block_funds" and any(
            self._server_key(server) in BLOCK_FUND_HOSTS for server in self.servers
        ):
            candidates = [
                item for item in candidates if self._server_key(item.server) in BLOCK_FUND_HOSTS
            ]
            if not candidates:
                raise NoHealthyServerError(
                    "no healthy supplemental quote server available for block_funds"
                )

        candidates.sort(
            key=lambda item: (
                item.active_connections,
                item.last_latency_ms if item.last_latency_ms is not None else 10_000.0,
                self._server_order[self._server_key(item.server)],
            )
        )
        return candidates[0].server

    def mark_success(self, server: ServerEndpoint, metrics: TransportMetrics) -> None:
        self.record_success(server, metrics)

    def record_success(self, server: ServerEndpoint, metrics: TransportMetrics) -> None:
        snapshot = self._health[self._server_key(server)]
        snapshot.success_count += 1
        snapshot.consecutive_failures = 0
        snapshot.last_latency_ms = metrics.last_latency_ms
        snapshot.cooldown_until_ms = 0.0
        snapshot.active_connections = self.connection_pool.active_count(server)
        snapshot.state = "healthy"

    def mark_failure(self, server: ServerEndpoint, exc: Exception) -> None:
        self.record_failure(server, exc)

    def record_failure(self, server: ServerEndpoint, exc: Exception) -> None:
        snapshot = self._health[self._server_key(server)]
        snapshot.failure_count += 1
        snapshot.consecutive_failures += 1
        snapshot.active_connections = self.connection_pool.active_count(server)
        if snapshot.consecutive_failures >= self.failure_threshold:
            snapshot.cooldown_until_ms = self._now_ms() + self.cooldown_ms
            snapshot.state = "cooldown"
        else:
            snapshot.state = "healthy"

    def snapshot(self) -> list[ServerHealthSnapshot]:
        now_ms = self._now_ms()
        snapshots: list[ServerHealthSnapshot] = []
        for server in self.servers:
            current = self._health[self._server_key(server)]
            snapshots.append(
                ServerHealthSnapshot(
                    server=current.server,
                    success_count=current.success_count,
                    failure_count=current.failure_count,
                    consecutive_failures=current.consecutive_failures,
                    last_latency_ms=current.last_latency_ms,
                    cooldown_until_ms=current.cooldown_until_ms,
                    active_connections=self.connection_pool.active_count(server),
                    state=self._current_state(current, now_ms),
                )
            )
        return snapshots
