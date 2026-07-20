from __future__ import annotations

import struct

import pytest

from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol.report_files import decode_ex_instrument_count
from mootdx_next.protocol.report_files import decode_report_file_chunk
from mootdx_next.protocol.report_files import encode_report_file


def test_encode_report_file_matches_expected_layout() -> None:
    payload = encode_report_file("tdxfin/gpcw.txt", offset=123)

    assert payload[:6] == bytes.fromhex("0C 12 34 00 00 00")
    raw_len_1, raw_len_2 = struct.unpack("<HH", payload[6:10])
    assert raw_len_1 == raw_len_2

    command, offset, node_size, filename = struct.unpack("<H2I100s", payload[10:10 + raw_len_1])
    assert command == 0x06B9
    assert offset == 123
    assert node_size == 0x7530
    assert filename.rstrip(b"\x00") == b"tdxfin/gpcw.txt"


def test_decode_report_file_chunk_returns_chunk_bytes() -> None:
    body = struct.pack("<I", 4) + b"test" + b"tail"
    result = decode_report_file_chunk(body)

    assert result["chunksize"] == 4
    assert result["chunkdata"] == b"test"


def test_decode_report_file_chunk_rejects_short_body() -> None:
    with pytest.raises(ProtocolDecodeError):
        decode_report_file_chunk(b"\x01\x02")


def test_decode_ex_instrument_count_reads_body_offset() -> None:
    body = b"\x00" * 19 + struct.pack("<I", 12345)
    assert decode_ex_instrument_count(body) == 12345


def test_decode_ex_instrument_count_rejects_short_body() -> None:
    with pytest.raises(ProtocolDecodeError):
        decode_ex_instrument_count(b"\x00" * 10)
