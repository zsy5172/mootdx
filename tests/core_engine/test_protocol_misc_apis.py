from __future__ import annotations

import struct

import pytest

from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol


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


def test_encode_index_bars_matches_bars_wire_shape() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_index_bars(9, 1, "000001", 1, 2) == protocol.encode_bars(9, 1, "000001", 1, 2)


def test_decode_index_bars_decodes_up_down_counts() -> None:
    protocol = StdQuoteProtocol()
    body = bytearray(struct.pack("<H", 1))
    body.extend(struct.pack("<I", 20260412))
    body.extend(b"\x00\x00\x00\x00")
    body.extend(struct.pack("<I", 0))
    body.extend(struct.pack("<I", 0))
    body.extend(struct.pack("<HH", 10, 5))

    rows = protocol.decode_index_bars(bytes(body), 9)

    assert len(rows) == 1
    assert rows[0]["open"] == 0.0
    assert rows[0]["close"] == 0.0
    assert rows[0]["high"] == 0.0
    assert rows[0]["low"] == 0.0
    assert rows[0]["year"] == 2026
    assert rows[0]["month"] == 4
    assert rows[0]["day"] == 12
    assert rows[0]["datetime"] == "2026-04-12 15:00"
    assert rows[0]["up_count"] == 10
    assert rows[0]["down_count"] == 5


def test_decode_block_info_meta_and_piece() -> None:
    protocol = StdQuoteProtocol()
    content = _build_block_content()
    meta_body = struct.pack("<I1s32s1s", len(content), b"\x00", b"x" * 32, b"\x00")
    piece_body = struct.pack("<I", len(content)) + content

    meta = protocol.decode_block_info_meta(meta_body)
    piece = protocol.decode_block_info(piece_body)

    assert meta["size"] == len(content)
    assert piece == content


def test_decode_block_info_rejects_truncated_declared_chunk() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError, match="truncated"):
        protocol.decode_block_info(struct.pack("<I", 5) + b"abc")


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01\x00short"])
def test_decode_index_bars_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_index_bars(body, 9)
