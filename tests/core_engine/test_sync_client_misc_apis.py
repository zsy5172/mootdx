from __future__ import annotations

import struct

import pytest

from mootdx_next.api.clients import BLOCK_CHUNK_SIZE
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


def _build_block_content() -> bytes:
    content = bytearray(b"\x00" * 384)
    content.extend(struct.pack("<H", 1))
    content.extend("测试板块".encode("gbk").ljust(9, b"\x00")[:9])
    content.extend(struct.pack("<HH", 2, 1))
    code_region = bytearray(b"\x00" * 2800)
    code_region[0:7] = b"600036\x00"
    code_region[7:14] = b"000001\x00"
    content.extend(code_region)
    return bytes(content)


def test_sync_client_index_bars_decodes_rows() -> None:
    body = bytearray(struct.pack("<H", 1))
    body.extend(struct.pack("<I", 20260412))
    body.extend(b"\x00\x00\x00\x00")
    body.extend(struct.pack("<I", 0))
    body.extend(struct.pack("<I", 0))
    body.extend(struct.pack("<HH", 10, 5))

    transport = RecordingTransport(responses=[bytes(body)])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.index_bars(symbol="000001", frequency=9, start=1, offset=2, market=1)

    assert rows[0]["up_count"] == 10
    assert rows[0]["down_count"] == 5


def test_sync_client_block_fetches_meta_and_piece() -> None:
    content = _build_block_content()
    meta_body = struct.pack("<I1s32s1s", len(content), b"\x00", b"x" * 32, b"\x00")
    piece_body = struct.pack("<I", len(content)) + content

    transport = RecordingTransport(responses=[meta_body, piece_body])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.block("block.dat")

    assert rows == [
        {"blockname": "测试板块", "block_type": 1, "code_index": 0, "code": "600036"},
        {"blockname": "测试板块", "block_type": 1, "code_index": 1, "code": "000001"},
    ]


def test_sync_client_block_file_uses_exact_chunk_sizes() -> None:
    total_size = BLOCK_CHUNK_SIZE + 2
    meta_body = struct.pack("<I1s32s1s", total_size, b"\x00", b"x" * 32, b"\x00")
    first = b"a" * BLOCK_CHUNK_SIZE
    second = b"bc"
    transport = RecordingTransport(
        responses=[
            meta_body,
            struct.pack("<I", len(first)) + first,
            struct.pack("<I", len(second)) + second,
        ]
    )
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.block_file_raw("block_gn.dat") == first + second
    windows = [struct.unpack_from("<II", payload, 12) for payload in transport.sent_payloads[1:]]
    assert windows == [(0, BLOCK_CHUNK_SIZE), (BLOCK_CHUNK_SIZE, 2)]


def test_sync_client_report_file_stops_after_short_chunk() -> None:
    first = b"a" * BLOCK_CHUNK_SIZE
    second = b"bc"
    transport = RecordingTransport(
        responses=[
            struct.pack("<I", len(first)) + first,
            struct.pack("<I", len(second)) + second,
        ]
    )
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.report_file("zhb.zip") == first + second
    windows = [struct.unpack_from("<II", payload, 12) for payload in transport.sent_payloads]
    assert windows == [(0, BLOCK_CHUNK_SIZE), (BLOCK_CHUNK_SIZE, BLOCK_CHUNK_SIZE)]


def test_sync_client_report_file_enforces_maximum_size() -> None:
    transport = RecordingTransport(
        responses=[struct.pack("<I", BLOCK_CHUNK_SIZE) + b"a" * BLOCK_CHUNK_SIZE]
    )
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(ProtocolDecodeError, match="exceeds"):
        client.report_file("large.zip", max_bytes=BLOCK_CHUNK_SIZE)


def test_sync_client_report_and_block_file_validate_names() -> None:
    client = SyncClient()

    with pytest.raises(ValueError, match="blank"):
        client.report_file("  ")
    with pytest.raises(ValueError, match="100"):
        client.block_file_raw("x" * 101)
