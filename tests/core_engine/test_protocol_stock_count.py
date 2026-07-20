from __future__ import annotations

import struct
from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def test_encode_stock_count_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()
    request_path = ROOT / "compat" / "corpus" / "stock_count" / "sh" / "steps" / "01_stock_count" / "request.bin"

    assert protocol.encode_stock_count(1) == request_path.read_bytes()


def test_decode_stock_count_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    body_path = ROOT / "compat" / "corpus" / "stock_count" / "sh" / "steps" / "01_stock_count" / "response.body.bin"
    expected_path = ROOT / "compat" / "corpus" / "stock_count" / "sh" / "expected.json"

    assert protocol.decode_stock_count(body_path.read_bytes()) == load_json(expected_path)["result"]


def test_encode_stock_count_rejects_unsupported_market() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(UnsupportedMarketError):
        protocol.encode_stock_count(9)


@pytest.mark.parametrize("body", [b"", b"\x01", struct.pack("<B", 5)])
def test_decode_stock_count_rejects_short_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_stock_count(body)
