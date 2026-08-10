from __future__ import annotations

import struct
from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next import TRADING_PHASES
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]

PCB_BLOCK_FUND_BODY = bytes.fromhex(
    "01000100013838303535308e12998427f9e101c7d201871bcefc019895800e1282ee94559cf704b3d79f520011b8e3ca1600"
    "d98427d1812791031502000100910100a0144f2000000000cc4e4ef830c0501566583f00000000000000000000000000000000"
    "0000000000000000000000000000b5039f52f16da94c9fc57e4b592d5921cc16ad12ae21ed1ee2118410b9383b3be91c401"
    "d8d3bcc474e1cf520c625831a9d13fa0e971f801dd910980fb438ef3aa61c441d3d455b50c32096257c00640084016501da09"
    "6a0a4357066a0201aa00aa00950023013a0135018b01"
)


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


def _legacy_projection(
    actual: list[dict[str, object]], expected: list[dict[str, object]]
) -> list[dict[str, object]]:
    return [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)]


def test_encode_quotes_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_quotes([(1, "600036")]) == _request_bytes("single_sh")
    assert protocol.encode_quotes([(1, "600036"), (0, "000001")]) == _request_bytes("mixed_batch")


def test_decode_quotes_matches_single_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _next_expected_records("single_sh")
    actual = protocol.decode_quotes(_response_body("single_sh"))

    assert _legacy_projection(actual, expected) == expected
    assert actual[0]["rate"] == actual[0]["reversed_bytes9"]
    assert actual[0]["rate_raw"] == round(float(actual[0]["rate"]) * 100)


def test_decode_quotes_matches_mixed_batch_expected() -> None:
    protocol = StdQuoteProtocol()

    rows = protocol.decode_quotes(_response_body("mixed_batch"))

    expected = _next_expected_records("mixed_batch")
    assert _legacy_projection(rows, expected) == expected
    assert [row["code"] for row in rows] == ["600036", "000001"]


def test_decode_quotes_scales_new_shanghai_etf_prefixes_as_funds() -> None:
    protocol = StdQuoteProtocol()
    body = bytearray(_response_body("single_sh"))
    body[5:11] = b"588000"

    row = protocol.decode_quotes(bytes(body))[0]

    assert row["code"] == "588000"
    assert row["price"] == pytest.approx(3.921)


def test_decode_quotes_exposes_numeric_trading_phase() -> None:
    rows = StdQuoteProtocol().decode_quotes(_response_body("mixed_batch"))

    assert [row["trading_phase"] for row in rows] == [5, 5]
    assert all("reversed_bytes4" not in row for row in rows)
    assert TRADING_PHASES[5] == "闭市阶段"


def test_encode_quotes_rejects_unsupported_market() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(UnsupportedMarketError):
        protocol.encode_quotes([(9, "600036")])


def test_encode_quotes_exposes_native_batch_limit() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError, match="at most 80.*quotes_all"):
        protocol.encode_quotes([(1, "600036")] * 81)


def test_encode_block_funds_matches_captured_mode_one_request() -> None:
    protocol = StdQuoteProtocol()
    expected = struct.pack(
        "<HIHHIIHH",
        0x010C,
        0x02006320,
        19,
        19,
        0x0005054C,
        0x00000100,
        0,
        1,
    ) + struct.pack("<B6s", 1, b"880550")

    assert protocol.encode_block_funds([(1, "880550")]) == expected


def test_decode_block_funds_matches_captured_pcb_row() -> None:
    row = StdQuoteProtocol().decode_block_funds(PCB_BLOCK_FUND_BODY)[0]

    assert row["code"] == "880550"
    assert row["price"] == pytest.approx(3197.69)
    assert row["amount"] == pytest.approx(343_259_316_224.0)
    assert row["short_turnover_rate"] == pytest.approx(4.01)
    assert row["amount_2min"] == pytest.approx(2_493_513_728.0)
    assert row["volume_growth_rate"] == pytest.approx(0.8453076482)
    assert row["main_buy_amount"] == pytest.approx(867_368_960.0)
    assert row["main_net_amount"] == pytest.approx(25_795_477_504.0)
    assert row["main_buy_share"] == pytest.approx(0.252686211)
    assert row["main_force_share"] == pytest.approx(7.514865958)
    assert row["super_large_net_amount"] == pytest.approx(20_980_592_447.32416)
    assert row["large_net_amount"] == pytest.approx(4_814_882_055.7824)
    assert row["medium_net_amount"] == pytest.approx(-4_384_615_999.73376)
    assert row["small_net_amount"] == pytest.approx(-21_410_858_503.3728)
    assert row["main_net_amount_5min"] == pytest.approx(744_428_573.1635201)
    assert row["main_force_share_5min"] == pytest.approx(0.2168706101)
    assert row["retail_order_growth_ratio"] == pytest.approx(-183.664085)
    assert row["fund_extension_available"] is True
    assert row["fund_extension_status"] == "available"


def test_block_funds_rejects_invalid_requests_and_responses() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError, match="at least one"):
        protocol.encode_block_funds([])
    with pytest.raises(ProtocolDecodeError, match="at most 80"):
        protocol.encode_block_funds([(1, "880550")] * 81)
    with pytest.raises(UnsupportedMarketError):
        protocol.encode_block_funds([(9, "880550")])
    with pytest.raises(ProtocolDecodeError, match="unexpected mode"):
        protocol.decode_block_funds(b"\x00\x00\x00\x00")
    with pytest.raises(ProtocolDecodeError, match="unconsumed bytes"):
        protocol.decode_block_funds(PCB_BLOCK_FUND_BODY + b"\x00")


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
