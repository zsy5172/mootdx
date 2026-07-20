from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _request(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "minutes" / case_id / "steps" / "01_minutes" / "request.bin").read_bytes()


def _body(case_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / "minutes" / case_id / "steps" / "01_minutes" / "response.body.bin").read_bytes()


def _expected(case_id: str) -> list[dict[str, object]]:
    return load_json(ROOT / "compat" / "corpus" / "minutes" / case_id / "expected.json")["result"]["records"]


def test_encode_minutes_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_minutes(0, "000001", "20171010") == _request("history_sh_000001_20171010")


def test_decode_minutes_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_minutes(_body("history_sh_000001_20171010"), 0, "000001") == _expected("history_sh_000001_20171010")


def test_decode_minutes_matches_empty_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_minutes(_body("history_empty_sz_159995_20200130"), 0, "159995") == _expected("history_empty_sz_159995_20200130")


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01\x00short"])
def test_decode_minutes_rejects_invalid_body(body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        protocol.decode_minutes(body, 0, "000001")
