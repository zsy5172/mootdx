from __future__ import annotations

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

    assert protocol.decode_bars(_body("daily_sh_600036_last10"), 9) == _expected("daily_sh_600036_last10")


def test_decode_bars_matches_intraday_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_bars(_body("intraday_5m_sh_600036_last20"), 0) == _expected("intraday_5m_sh_600036_last20")


def test_decode_bars_matches_bj_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_bars(_body("daily_bj_430090_last10"), 9) == _expected("daily_bj_430090_last10")


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01\x00short"])
def test_decode_bars_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_bars(body, 9)
