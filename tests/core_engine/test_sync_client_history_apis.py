from __future__ import annotations

from pathlib import Path

import pytest
from freezegun import freeze_time

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import TransportTimeoutError
from mootdx_next.models import RequestContext
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport

ROOT = Path(__file__).resolve().parents[2]


def _bars_body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "bars" / case_id / "steps" / "01_bars" / "response.body.bin").read_bytes()


def _minutes_body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "minutes" / case_id / "steps" / "01_minutes" / "response.body.bin").read_bytes()


def test_sync_client_bars_decodes_daily_rows() -> None:
    transport = RecordingTransport(responses=[_bars_body("daily_sh_600036_last10")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.bars(symbol="600036", frequency="day", offset=10)

    assert len(rows) == 10
    assert rows[0]["datetime"]
    assert rows[0]["volume"] == rows[0]["vol"]


def test_sync_client_bars_supports_bj_symbols() -> None:
    transport = RecordingTransport(responses=[_bars_body("daily_bj_430090_last10")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.bars(symbol="430090", frequency="day", offset=10)

    assert rows == []
    payload = transport.sent_payloads[0]
    assert payload[12:14] == (2).to_bytes(2, "little")


def test_sync_client_minutes_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_minutes_body("history_sh_000001_20171010")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.minutes(symbol="000001", date="20171010")

    assert len(rows) == 240
    assert rows[0]["volume"] == rows[0]["vol"]


def test_sync_client_minutes_returns_empty_for_empty_history() -> None:
    transport = RecordingTransport(responses=[_minutes_body("history_empty_sz_159995_20200130")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.minutes(symbol="159995", date="20200130") == []


@freeze_time("2026-04-11 10:00:00")
def test_sync_client_minute_wraps_minutes_for_today() -> None:
    transport = RecordingTransport(responses=[_minutes_body("history_sh_000001_20171010")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    client.minute("000001")

    payload = transport.sent_payloads[0]
    assert int.from_bytes(payload[12:16], "little") == 20260411


def test_sync_client_rejects_invalid_history_params() -> None:
    client = SyncClient()

    with pytest.raises(InvalidSymbolError):
        client.bars(symbol="", frequency="day")
    with pytest.raises(InvalidFrequencyError):
        client.bars(symbol="600036", frequency="bad")
    with pytest.raises(ValueError):
        client.bars(symbol="600036", start=-1)
    with pytest.raises(ValueError, match="65535"):
        client.bars(symbol="600036", start=65536)
    with pytest.raises(ValueError):
        client.bars(symbol="600036", offset=0)
    with pytest.raises(InvalidDateError):
        client.minutes(symbol="000001", date="2026/04/11")


def test_sync_client_index_bars_honors_market_prefixes_and_rejects_conflicts() -> None:
    transport = RecordingTransport(responses=[b"\x00\x00"] * 3)
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.index_bars("sh000001") == []
    assert client.index_bars("sz399001") == []
    assert client.index_bars("bj899050") == []
    assert [int.from_bytes(payload[12:14], "little") for payload in transport.sent_payloads] == [1, 0, 2]

    with pytest.raises(InvalidSymbolError, match="conflicts"):
        client.index_bars("sh000001", market=0)
    with pytest.raises(ValueError, match="65535"):
        client.index_bars("sh000001", start=65536)


def test_sync_client_minutes_supports_bj_symbol() -> None:
    transport = RecordingTransport(responses=[b"\x00\x00"])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.minutes(symbol="bj430090", date="20200101") == []
    assert transport.sent_payloads[0][16] == 2


def test_sync_client_history_apis_propagate_scheduler_failure() -> None:
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server, select_error=NoHealthyServerError("no server"))
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(NoHealthyServerError):
        client.bars(symbol="600036")

    with pytest.raises(NoHealthyServerError):
        client.minutes(symbol="000001", date="20171010")


def test_sync_client_history_apis_discard_on_transport_failure() -> None:
    transport = RecordingTransport(send_error=TransportTimeoutError("timed out"))
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(TransportTimeoutError):
        client.bars(symbol="600036")

    assert len(pool.discarded) == 2


def test_sync_client_default_scheduler_wires_history_server() -> None:
    client = SyncClient()

    assert client.scheduler.select_server(RequestContext(api="bars")) is not None
    assert client.scheduler.select_server(RequestContext(api="minutes")) is not None
