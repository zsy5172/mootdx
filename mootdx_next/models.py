from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mootdx_next.interfaces import AbstractTransport


@dataclass(slots=True)
class ServerEndpoint:
    host: str
    port: int
    label: str | None = None
    market: str | None = None


@dataclass(slots=True)
class RequestContext:
    api: str
    params: dict[str, object] = field(default_factory=dict)
    timeout_ms: int = 15000
    request_id: str | None = None


@dataclass(slots=True)
class ResponseHeader:
    raw: bytes
    reserved_1: int
    reserved_2: int
    reserved_3: int
    compressed_size: int
    uncompressed_size: int


@dataclass(slots=True)
class ResponseEnvelope:
    header: ResponseHeader | None = None
    body: bytes | None = None
    server: ServerEndpoint | None = None
    elapsed_ms: float | None = None


@dataclass(slots=True)
class TransportMetrics:
    sent_requests: int = 0
    failed_requests: int = 0
    retry_count: int = 0
    reconnect_count: int = 0
    connect_count: int = 0
    heartbeat_count: int = 0
    timeout_count: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    last_latency_ms: float | None = None


@dataclass(slots=True)
class ConnectionLease:
    server: ServerEndpoint
    transport: "AbstractTransport"
    created_at_ms: float
    last_used_ms: float


@dataclass(slots=True)
class ServerHealthSnapshot:
    server: ServerEndpoint
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_latency_ms: float | None = None
    cooldown_until_ms: float = 0.0
    active_connections: int = 0
    state: str = "healthy"


@dataclass(slots=True)
class ConnectionPoolSnapshot:
    total_active: int = 0
    total_idle: int = 0
    created_count: int = 0
    reused_count: int = 0
    exhausted_count: int = 0
