from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next import TRADING_PHASES
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


def _next_expected_records(case_id: str) -> list[dict[str, object]]:
    expected = load_json(ROOT / "compat" / "corpus" / "quotes" / case_id / "expected.json")
    records = expected["result"]["records"]
    for record in records:
        (trading_status_word,) = record.pop("reversed_bytes4")
        record["trading_phase"] = (trading_status_word >> 2) & 0x0F
    return records


def test_encode_quotes_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_quotes([(1, "600036")]) == _request_bytes("single_sh")
    assert protocol.encode_quotes([(1, "600036"), (0, "000001")]) == _request_bytes("mixed_batch")


def test_decode_quotes_matches_single_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_quotes(_response_body("single_sh")) == _next_expected_records("single_sh")


def test_decode_quotes_matches_mixed_batch_expected() -> None:
    protocol = StdQuoteProtocol()

    rows = protocol.decode_quotes(_response_body("mixed_batch"))

    assert rows == _next_expected_records("mixed_batch")
    assert [row["code"] for row in rows] == ["600036", "000001"]


def test_decode_quotes_exposes_numeric_trading_phase() -> None:
    rows = StdQuoteProtocol().decode_quotes(_response_body("mixed_batch"))

    assert [row["trading_phase"] for row in rows] == [5, 5]
    assert all("reversed_bytes4" not in row for row in rows)
    assert TRADING_PHASES[5] == "闭市阶段"


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
