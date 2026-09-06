from __future__ import annotations

import struct
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


def _encode_price(value: int) -> bytes:
    remaining = abs(value)
    encoded = bytearray([(remaining & 0x3F) | (0x40 if value < 0 else 0)])
    remaining >>= 6
    while remaining:
        encoded[-1] |= 0x80
        encoded.append(remaining & 0x7F)
        remaining >>= 7
    return bytes(encoded)


def _current_minutes_body() -> bytes:
    return struct.pack("<HH", 1, 0) + b"".join(
        _encode_price(value) for value in (1000, 100000, 10)
    )


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
def test_sync_client_minute_uses_current_minute_command() -> None:
    transport = RecordingTransport(responses=[_current_minutes_body()])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.minute("000001")

    payload = transport.sent_payloads[0]
    assert payload[:12] == bytes.fromhex("0c02080001000e000e003705")
    assert transport.contexts[0].api == "minute"
    assert rows[0]["datetime"] == "2026-04-11 09:31"


@freeze_time("2026-09-04 10:00:00")
def test_sync_client_latest_minutes_uses_current_data_for_today(monkeypatch) -> None:
    client = SyncClient()
    calls: list[str] = []
    monkeypatch.setattr(
        client,
        "bars",
        lambda *args, **kwargs: [{"datetime": "2026-09-04 15:00"}],
    )
    monkeypatch.setattr(
        client,
        "minute",
        lambda symbol: calls.append("minute") or [{"price": 41.0, "datetime": "2026-09-04 09:31"}],
    )
    monkeypatch.setattr(
        client,
        "minutes",
        lambda symbol, date: calls.append(f"minutes:{date}") or [],
    )

    rows = client.latest_minutes("600036")

    assert rows[0]["price"] == 41.0
    assert calls == ["minute"]


@freeze_time("2026-09-04 08:00:00")
def test_sync_client_latest_minutes_uses_latest_historical_trade_day(monkeypatch) -> None:
    client = SyncClient()
    calls: list[str] = []
    monkeypatch.setattr(
        client,
        "bars",
        lambda *args, **kwargs: [{"datetime": "116785687-01-01 15:00"}],
    )
    monkeypatch.setattr(
        client,
        "index_bars",
        lambda *args, **kwargs: [{"datetime": "2026-09-03 15:00"}],
    )
    monkeypatch.setattr(
        client,
        "minute",
        lambda symbol: calls.append("minute") or [],
    )
    monkeypatch.setattr(
        client,
        "minutes",
        lambda symbol, date: calls.append(f"minutes:{date}")
        or [{"price": 40.91, "datetime": "2026-09-03 09:31"}],
    )

    rows = client.latest_minutes("sh000001")

    assert rows[0]["datetime"].startswith("2026-09-03")
    assert calls == ["minutes:20260903"]


@freeze_time("2026-09-04 08:00:00")
def test_sync_client_latest_minutes_rejects_live_placeholder(monkeypatch) -> None:
    client = SyncClient()
    calls: list[str] = []
    monkeypatch.setattr(
        client,
        "bars",
        lambda *args, **kwargs: [{"datetime": "2026-09-04 15:00"}],
    )
    monkeypatch.setattr(
        client,
        "minute",
        lambda symbol: calls.append("minute") or [{"price": 0.0, "datetime": "2026-09-04 09:31"}],
    )
    monkeypatch.setattr(
        client,
        "minutes",
        lambda symbol, date: calls.append(f"minutes:{date}") or [],
    )

    assert client.latest_minutes("600036") == []
    assert calls == ["minute", "minutes:20260904"]


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
