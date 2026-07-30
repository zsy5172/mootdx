from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from compat.common import load_json
from mootdx_next.adapters import xdxr_to_frame
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _request(api: str, case_id: str, step_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step_id / "request.bin").read_bytes()


def _body(api: str, case_id: str, step_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step_id / "response.body.bin").read_bytes()


def _result(api: str, case_id: str):
    return load_json(ROOT / "compat" / "corpus" / api / case_id / "expected.json")["result"]


def test_encode_finance_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()
    assert protocol.encode_finance(0, "000001") == _request("finance", "sz_000001", "01_finance")


def test_decode_finance_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _result("finance", "sz_000001")["records"][0]
    actual = protocol.decode_finance(_body("finance", "sz_000001", "01_finance"))
    assert {key: actual[key] for key in expected} == expected
    assert actual["touzishouyi"] == actual["touzishouyu"]


def test_encode_xdxr_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()
    assert protocol.encode_xdxr(1, "600036") == _request("xdxr", "sh_600036", "01_xdxr")


def test_encode_xdxr_accepts_bj_market_two() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_xdxr(2, '920001')[-7:] == b'\x02' + b'920001'


def test_decode_xdxr_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _result("xdxr", "sh_600036")
    actual = xdxr_to_frame(protocol.decode_xdxr(_body("xdxr", "sh_600036", "01_xdxr")))
    expected_df = pd.DataFrame(expected["records"])
    assert_frame_equal(actual, expected_df, check_dtype=False)


def test_encode_f10_categories_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()
    assert protocol.encode_f10_categories(1, "600036") == _request(
        "f10_categories", "sh_600036", "01_f10_categories"
    )


def test_decode_f10_categories_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _result("f10_categories", "sh_600036")["records"]
    assert protocol.decode_f10_categories(_body("f10_categories", "sh_600036", "01_f10_categories")) == expected


def test_encode_f10_content_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()
    manifest = load_json(ROOT / "compat" / "corpus" / "f10_content" / "sh_600036__latest_tip" / "manifest.json")
    kwargs = manifest["steps"][1]["kwargs"]
    assert protocol.encode_f10_content(1, "600036", kwargs["filename"], kwargs["start"], kwargs["length"]) == _request(
        "f10_content", "sh_600036__latest_tip", "02_f10_content"
    )


def test_decode_f10_content_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = load_json(ROOT / "compat" / "corpus" / "f10_content" / "sh_600036__latest_tip" / "expected.json")["result"]
    assert protocol.decode_f10_content(_body("f10_content", "sh_600036__latest_tip", "02_f10_content")) == expected


@pytest.mark.parametrize(
    ("decoder", "body"),
    [
        ("finance", b""),
        ("finance", b"\x01"),
        ("f10_categories", b""),
        ("f10_categories", b"\x01"),
        ("f10_content", b""),
        ("f10_content", b"\x01"),
    ],
)
def test_info_decoders_reject_invalid_body(decoder: str, body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        if decoder == "finance":
            protocol.decode_finance(body)
        elif decoder == "xdxr":
            protocol.decode_xdxr(body)
        elif decoder == "f10_categories":
            protocol.decode_f10_categories(body)
        else:
            protocol.decode_f10_content(body)


def test_decode_xdxr_returns_empty_for_short_body() -> None:
    protocol = StdQuoteProtocol()
    assert protocol.decode_xdxr(b"\x01") == []
