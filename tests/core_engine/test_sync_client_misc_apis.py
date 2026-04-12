from __future__ import annotations

import struct

from mootdx_next.api.clients import SyncClient
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
    piece_body = b"\x00\x00\x00\x00" + content

    transport = RecordingTransport(responses=[meta_body, piece_body])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.block("block.dat")

    assert rows == [
        {"blockname": "测试板块", "block_type": 1, "code_index": 0, "code": "600036"},
        {"blockname": "测试板块", "block_type": 1, "code_index": 1, "code": "000001"},
    ]
