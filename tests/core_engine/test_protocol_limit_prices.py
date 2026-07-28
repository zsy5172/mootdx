from __future__ import annotations

import math
import struct

import pytest

from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

# Rows selected without changing their bytes from the 2026-07-28 0x0452 capture.
CAPTURED_RESPONSE_SUBSET = bytes.fromhex(
    "03 00 "
    "00 0a 00 00 00 29 5c cf 3f c3 f5 a8 3f "
    "00 ea 93 04 00 33 33 13 40 b8 1e c5 3f "
    "01 f5 27 09 00 00 00 f0 40 e1 7a c4 40"
)


def test_encode_limit_prices_matches_captured_request() -> None:
    request = bytes.fromhex("00 00 00 00 00 00 10 00 10 00 52 04 00 00 00 00 d0 07 00 00 00 00 00 00 00 00")

    assert StdQuoteProtocol().encode_limit_prices(start=0, count=2000) == request


def test_encode_limit_prices_places_page_window_in_wire_fields() -> None:
    request = StdQuoteProtocol().encode_limit_prices(start=123, count=456)

    assert struct.unpack("<HIHH", request[:10]) == (0, 0, 16, 16)
    assert struct.unpack("<8H", request[10:]) == (0x0452, 123, 0, 456, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("start", "count"),
    [(-1, 1), (65536, 1), (0, 0), (0, 2001)],
)
def test_encode_limit_prices_rejects_invalid_window(start: int, count: int) -> None:
    with pytest.raises(ValueError):
        StdQuoteProtocol().encode_limit_prices(start=start, count=count)


def test_decode_limit_prices_matches_captured_values() -> None:
    rows = StdQuoteProtocol().decode_limit_prices(CAPTURED_RESPONSE_SUBSET)

    assert rows == [
        {"market": 0, "code": "000010", "limit_up": 1.62, "limit_down": 1.32},
        {"market": 0, "code": "300010", "limit_up": 2.3, "limit_down": 1.54},
        {"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14},
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x01",
        struct.pack("<H", 1),
        struct.pack("<H", 0) + b"\x00",
        struct.pack("<HBIff", 1, 0, 10, math.nan, 1.0),
    ],
)
def test_decode_limit_prices_rejects_malformed_body(body: bytes) -> None:
    with pytest.raises(ProtocolDecodeError):
        StdQuoteProtocol().decode_limit_prices(body)
