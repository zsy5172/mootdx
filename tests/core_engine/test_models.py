from mootdx_next.models import ConnectionLease
from mootdx_next.models import ConnectionPoolSnapshot
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ResponseHeader
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import ServerHealthSnapshot
from mootdx_next.models import TransportMetrics


def test_server_endpoint_defaults() -> None:
    endpoint = ServerEndpoint(host="127.0.0.1", port=7709)
    assert endpoint.label is None
    assert endpoint.market is None


def test_request_context_defaults() -> None:
    context = RequestContext(api="stock_count")
    assert context.params == {}
    assert context.timeout_ms == 15000
    assert context.request_id is None


def test_response_envelope_defaults() -> None:
    envelope = ResponseEnvelope()
    assert envelope.header is None
    assert envelope.body is None
    assert envelope.server is None
    assert envelope.elapsed_ms is None


def test_response_header_fields() -> None:
    header = ResponseHeader(
        raw=b"1234567890abcdef",
        reserved_1=1,
        reserved_2=2,
        reserved_3=3,
        compressed_size=4,
        uncompressed_size=5,
    )
    assert header.raw == b"1234567890abcdef"
    assert header.reserved_1 == 1
    assert header.reserved_2 == 2
    assert header.reserved_3 == 3
    assert header.compressed_size == 4
    assert header.uncompressed_size == 5


def test_transport_metrics_defaults() -> None:
    metrics = TransportMetrics()
    assert metrics.sent_requests == 0
    assert metrics.failed_requests == 0
    assert metrics.retry_count == 0
    assert metrics.reconnect_count == 0
    assert metrics.connect_count == 0
    assert metrics.heartbeat_count == 0
    assert metrics.timeout_count == 0
    assert metrics.bytes_sent == 0
    assert metrics.bytes_received == 0
    assert metrics.last_latency_ms is None


def test_connection_lease_fields() -> None:
    endpoint = ServerEndpoint(host="127.0.0.1", port=7709)
    lease = ConnectionLease(server=endpoint, transport=object(), created_at_ms=1.0, last_used_ms=2.0)
    assert lease.server == endpoint
    assert lease.created_at_ms == 1.0
    assert lease.last_used_ms == 2.0


def test_server_health_snapshot_defaults() -> None:
    snapshot = ServerHealthSnapshot(server=ServerEndpoint(host="127.0.0.1", port=7709))
    assert snapshot.success_count == 0
    assert snapshot.failure_count == 0
    assert snapshot.consecutive_failures == 0
    assert snapshot.last_latency_ms is None
    assert snapshot.cooldown_until_ms == 0.0
    assert snapshot.active_connections == 0
    assert snapshot.state == "healthy"


def test_connection_pool_snapshot_defaults() -> None:
    snapshot = ConnectionPoolSnapshot()
    assert snapshot.total_active == 0
    assert snapshot.total_idle == 0
    assert snapshot.created_count == 0
    assert snapshot.reused_count == 0
    assert snapshot.exhausted_count == 0
