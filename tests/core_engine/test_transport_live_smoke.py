import pytest

from mootdx_next.errors import EmptyResponseError
from mootdx_next.errors import InvalidResponseHeaderError
from mootdx_next.errors import PayloadDecompressionError
from mootdx_next.errors import TransportConnectionError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.transport.socket_transport import SyncSocketTransport
from tests.core_engine.support import PREFERRED_HQ_HOSTS
from tests.core_engine.support import build_stock_count_request


@pytest.mark.network
def test_sync_socket_transport_live_smoke() -> None:
    payload = build_stock_count_request()
    context = RequestContext(api="stock_count", timeout_ms=1500)
    errors: list[str] = []

    for label, host, port in PREFERRED_HQ_HOSTS:
        transport = SyncSocketTransport()
        server = ServerEndpoint(host=host, port=port, label=label)
        try:
            envelope = transport.send(context, payload, server)
            assert envelope.header is not None
            assert envelope.body
            assert envelope.elapsed_ms is not None
            assert envelope.server == server
            return
        except (
            TransportConnectionError,
            TransportTimeoutError,
            EmptyResponseError,
            InvalidResponseHeaderError,
            PayloadDecompressionError,
        ) as exc:
            errors.append(f"{label}({host}:{port}): {type(exc).__name__}: {exc}")
        finally:
            transport.close()

    pytest.skip("no reachable HQ host for transport smoke: " + "; ".join(errors))
