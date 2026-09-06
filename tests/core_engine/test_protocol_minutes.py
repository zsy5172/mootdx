from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _encode_price(value: int) -> bytes:
    remaining = abs(value)
    encoded = bytearray([(remaining & 0x3F) | (0x40 if value < 0 else 0)])
    remaining >>= 6
    while remaining:
        encoded[-1] |= 0x80
        encoded.append(remaining & 0x7F)
        remaining >>= 7
    return bytes(encoded)


def _request(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "minutes" / case_id / "steps" / "01_minutes" / "request.bin").read_bytes()


def _body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "minutes" / case_id / "steps" / "01_minutes" / "response.body.bin").read_bytes()


def _expected(case_id: str) -> list[dict[str, object]]:
    return load_json(ROOT / "compat" / "corpus" / "minutes" / case_id / "expected.json")["result"]["records"]


def test_encode_minutes_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_minutes(0, "000001", "20171010") == _request("history_sh_000001_20171010")


def test_encode_current_minute_matches_protocol_request() -> None:
    protocol = StdQuoteProtocol()

    expected = bytes.fromhex("0c02080001000e000e003705")
    expected += bytes.fromhex("0100") + b"600036" + bytes(2) + bytes.fromhex("f000")

    assert protocol.encode_minute(1, "600036") == expected
    assert protocol.encode("minute", market=1, code="600036") == expected


def test_decode_minutes_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _expected("history_sh_000001_20171010")
    actual = protocol.decode_minutes(_body("history_sh_000001_20171010"), 0, "000001")

    assert [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)] == expected
    assert actual[0]["time"] == "09:31"
    assert actual[119]["time"] == "11:30"
    assert actual[120]["time"] == "13:01"
    assert actual[-1]["time"] == "15:00"


def test_decode_current_minute_uses_four_byte_prefix() -> None:
    protocol = StdQuoteProtocol()
    encoded_rows = b"".join(
        _encode_price(value)
        for value in (
            1000,
            100000,
            10,
            5,
            100,
            20,
            -2,
            -50,
            30,
        )
    )
    current_body = bytes.fromhex("03000000") + encoded_rows

    current = protocol.decode_minute(
        current_body,
        0,
        "000001",
        date="20171010",
    )
    assert [row["price"] for row in current] == pytest.approx([10.0, 10.05, 9.98])
    assert [row["average_price"] for row in current] == pytest.approx([10.0, 10.01, 9.995])
    assert [row["volume"] for row in current] == [10, 20, 30]
    assert current[0]["datetime"] == "2017-10-10 09:31"


def test_decode_minutes_adds_requested_date_and_scales_new_shanghai_etf() -> None:
    protocol = StdQuoteProtocol()

    rows = protocol.decode_minutes(
        _body("history_sh_000001_20171010"),
        1,
        "588000",
        date="20171010",
    )

    assert rows[0]["price"] == pytest.approx(1.135)
    assert rows[0]["datetime"] == "2017-10-10 09:31"


def test_decode_minutes_matches_empty_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_minutes(_body("history_empty_sz_159995_20200130"), 0, "159995") == _expected("history_empty_sz_159995_20200130")


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01\x00short"])
def test_decode_minutes_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_minutes(body, 0, "000001")
