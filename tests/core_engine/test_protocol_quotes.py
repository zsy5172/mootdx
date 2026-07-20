from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _request_bytes(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "quotes" / case_id / "steps" / "01_quotes" / "request.bin").read_bytes()


def _response_body(case_id: str) -> bytes:
    return (
        ROOT / "compat" / "corpus" / "quotes" / case_id / "steps" / "01_quotes" / "response.body.bin"
    ).read_bytes()


def test_encode_quotes_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_quotes([(1, "600036")]) == _request_bytes("single_sh")
    assert protocol.encode_quotes([(1, "600036"), (0, "000001")]) == _request_bytes("mixed_batch")


def test_decode_quotes_matches_single_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = load_json(ROOT / "compat" / "corpus" / "quotes" / "single_sh" / "expected.json")

    assert protocol.decode_quotes(_response_body("single_sh")) == expected["result"]["records"]


def test_decode_quotes_matches_mixed_batch_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = load_json(ROOT / "compat" / "corpus" / "quotes" / "mixed_batch" / "expected.json")

    rows = protocol.decode_quotes(_response_body("mixed_batch"))

    assert rows == expected["result"]["records"]
    assert [row["code"] for row in rows] == ["600036", "000001"]


def test_encode_quotes_rejects_unsupported_market() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(UnsupportedMarketError):
        protocol.encode_quotes([(9, "600036")])


@pytest.mark.parametrize("body", [b"", b"\x00", b"\x00\x00\x01"])
def test_decode_quotes_rejects_short_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_quotes(body)


def test_decode_quotes_rejects_truncated_row() -> None:
    protocol = StdQuoteProtocol()
    body = b"\xb1\xcb\x01\x00" + b"\x00" * 20

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_quotes(body)
