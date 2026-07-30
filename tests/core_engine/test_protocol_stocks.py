from __future__ import annotations

import struct
from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _page_body(case_id: str, step_id: str) -> bytes:
    return (
        ROOT / "compat" / "corpus" / "stocks" / case_id / "steps" / step_id / "response.body.bin"
    ).read_bytes()


def _request_bytes(case_id: str, step_id: str) -> bytes:
    return (
        ROOT / "compat" / "corpus" / "stocks" / case_id / "steps" / step_id / "request.bin"
    ).read_bytes()


def _without_name_padding(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            **row,
            "name": str(row["name"]).split("\x00", 1)[0],
        }
        for row in rows
    ]


def test_encode_stock_list_page_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_stock_list_page(1, 0) == _request_bytes("sh_full_market", "02_list_00000")
    assert protocol.encode_stock_list_page(1, 1000) == _request_bytes("sh_full_market", "03_list_01000")


def test_decode_single_stock_list_page_matches_expected_slice() -> None:
    protocol = StdQuoteProtocol()
    body = _page_body("sh_full_market", "02_list_00000")
    expected = load_json(ROOT / "compat" / "corpus" / "stocks" / "sh_full_market" / "expected.json")

    rows = protocol.decode_stock_list_page(body)

    assert rows == _without_name_padding(expected["result"]["records"][: len(rows)])


def test_decode_multiple_stock_list_pages_matches_expected_slice() -> None:
    protocol = StdQuoteProtocol()
    expected = load_json(ROOT / "compat" / "corpus" / "stocks" / "sh_full_market" / "expected.json")

    rows = protocol.decode_stock_list_pages(
        [
            _page_body("sh_full_market", "02_list_00000"),
            _page_body("sh_full_market", "03_list_01000"),
        ]
    )

    assert rows == _without_name_padding(expected["result"]["records"][: len(rows)])


def test_decode_stock_list_page_strips_fixed_width_name_padding() -> None:
    body = struct.pack("<H6sH8s4sBI4s", 1, b"588000", 100, b"ETF\x00\x00\x00\x00\x00", b"\x00" * 4, 3, 0, b"\x00" * 4)

    assert StdQuoteProtocol().decode_stock_list_page(body)[0]["name"] == "ETF"


def test_decode_stock_list_page_zero_rows_returns_empty_list() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_stock_list_page(struct.pack("<H", 0)) == []


def test_encode_stock_list_page_rejects_invalid_market() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(UnsupportedMarketError):
        protocol.encode_stock_list_page(2, 0)


def test_encode_stock_list_page_rejects_negative_start() -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.encode_stock_list_page(1, -1)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x01",
        struct.pack("<H", 1) + b"short",
    ],
)
def test_decode_stock_list_page_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_stock_list_page(body)
