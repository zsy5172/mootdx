from __future__ import annotations

from pathlib import Path

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.models import RequestContext
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport

ROOT = Path(__file__).resolve().parents[2]


def test_sync_client_quotes_returns_empty_for_empty_input() -> None:
    client = SyncClient()

    assert client.quotes(None) == []
    assert client.quotes("") == []
    assert client.quotes([]) == []


def test_sync_client_quotes_decodes_single_symbol() -> None:
    response = (ROOT / "compat" / "corpus" / "quotes" / "single_sh" / "steps" / "01_quotes" / "response.body.bin").read_bytes()
    transport = RecordingTransport(responses=[response])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.quotes("600036")

    assert len(rows) == 1
    assert rows[0]["code"] == "600036"
    assert rows[0]["market"] == 1
    assert len(pool.released) == 1


def test_sync_client_quotes_preserves_input_order() -> None:
    response = (ROOT / "compat" / "corpus" / "quotes" / "mixed_batch" / "steps" / "01_quotes" / "response.body.bin").read_bytes()
    transport = RecordingTransport(responses=[response])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.quotes(["600036", "000001"])

    assert [row["code"] for row in rows] == ["600036", "000001"]


def test_sync_client_quotes_preserves_prefixed_market_selection() -> None:
    response = (ROOT / "compat" / "corpus" / "quotes" / "mixed_batch" / "steps" / "01_quotes" / "response.body.bin").read_bytes()
    transport = RecordingTransport(responses=[response])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    client.quotes(["sh600036", "sz000001"])

    payload = transport.sent_payloads[0]
    assert payload[-14:-7] == bytes([1]) + b"600036"
    assert payload[-7:] == bytes([0]) + b"000001"


def test_sync_client_quotes_rejects_invalid_symbol_input() -> None:
    client = SyncClient()

    with pytest.raises(InvalidSymbolError):
        client.quotes(123)  # type: ignore[arg-type]

    with pytest.raises(InvalidSymbolError):
        client.quotes(["600036", 123])  # type: ignore[list-item]


def test_sync_client_quotes_propagates_scheduler_failure() -> None:
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server, select_error=NoHealthyServerError("no server"))
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(NoHealthyServerError):
        client.quotes("600036")


def test_sync_client_quotes_discards_lease_on_transport_failure() -> None:
    transport = RecordingTransport(send_error=TransportTimeoutError("timed out"))
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(TransportTimeoutError):
        client.quotes("600036")

    assert not pool.released
    assert len(pool.discarded) == 2
    assert len(scheduler.failure_calls) == 2


def test_sync_client_quotes_default_scheduler_wires_server() -> None:
    client = SyncClient()

    assert client.scheduler.select_server(RequestContext(api="quotes")) is not None
