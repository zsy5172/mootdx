from __future__ import annotations

import struct
from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _request(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "bars" / case_id / "steps" / "01_bars" / "request.bin").read_bytes()


def _body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "bars" / case_id / "steps" / "01_bars" / "response.body.bin").read_bytes()


def _expected(case_id: str) -> list[dict[str, object]]:
    return load_json(ROOT / "compat" / "corpus" / "bars" / case_id / "expected.json")["result"]["records"]


def test_encode_bars_matches_daily_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_bars(9, 1, "600036", 0, 10) == _request("daily_sh_600036_last10")


def test_encode_bars_matches_intraday_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_bars(0, 1, "600036", 0, 20) == _request("intraday_5m_sh_600036_last20")


def test_decode_bars_matches_daily_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _expected("daily_sh_600036_last10")
    actual = protocol.decode_bars(_body("daily_sh_600036_last10"), 9)

    assert [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)] == expected
    assert actual[0]["previous_close"] is None
    assert actual[1]["previous_close"] == actual[0]["close"]


def test_decode_bars_matches_intraday_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _expected("intraday_5m_sh_600036_last20")
    for row in expected:
        row["vol"] /= 100
        row["volume"] /= 100
    actual = protocol.decode_bars(_body("intraday_5m_sh_600036_last20"), 0)

    assert [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)] == expected


def test_decode_bars_matches_bj_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_bars(_body("daily_bj_430090_last10"), 9) == _expected("daily_bj_430090_last10")


def _single_bar_body(*, frequency: int, index: bool, volume_raw: int = 0x4A123456) -> bytes:
    body = bytearray(struct.pack("<H", 1))
    if frequency in {0, 1, 2, 3, 7, 8}:
        body.extend(struct.pack("<HH", (2026 - 2004) * 2048 + 7 * 100 + 30, 9 * 60 + 31))
    else:
        body.extend(struct.pack("<I", 20260730))
    body.extend(b"\x00\x00\x00\x00")
    body.extend(struct.pack("<I", volume_raw))
    body.extend(struct.pack("<I", 0))
    if index:
        body.extend(struct.pack("<HH", 123, 45))
    return bytes(body)


def test_decode_index_daily_volume_uses_public_lot_unit() -> None:
    protocol = StdQuoteProtocol()
    security = protocol.decode_bars(_single_bar_body(frequency=9, index=False), 9)
    index = protocol.decode_index_bars(_single_bar_body(frequency=9, index=True), 9)

    assert index[0]["volume"] == security[0]["volume"] * 100


def test_decode_index_intraday_volume_does_not_apply_daily_factor() -> None:
    protocol = StdQuoteProtocol()
    security = protocol.decode_bars(_single_bar_body(frequency=8, index=False), 8)
    index = protocol.decode_index_bars(_single_bar_body(frequency=8, index=True), 8)

    assert index[0]["volume"] == security[0]["volume"] * 100


def test_decode_alternate_daily_volume_normalizes_share_encoding_to_lots() -> None:
    protocol = StdQuoteProtocol()
    alternate = protocol.decode_bars(_single_bar_body(frequency=4, index=False), 4)
    regular = protocol.decode_bars(_single_bar_body(frequency=9, index=False), 9)

    assert alternate[0]["volume"] * 100 == regular[0]["volume"]


@pytest.mark.parametrize("index", [False, True])
def test_decode_zero_volume_and_amount_are_exact_zero(index: bool) -> None:
    protocol = StdQuoteProtocol()
    body = _single_bar_body(frequency=8, index=index, volume_raw=0)

    rows = (
        protocol.decode_index_bars(body, 8)
        if index
        else protocol.decode_bars(body, 8)
    )

    assert rows[0]["volume"] == 0
    assert rows[0]["amount"] == 0


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01\x00short"])
def test_decode_bars_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_bars(body, 9)
