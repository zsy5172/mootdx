from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]


def _request(api: str, case_id: str, step_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step_id / "request.bin").read_bytes()


def _body(api: str, case_id: str, step_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step_id / "response.body.bin").read_bytes()


def _expected(api: str, case_id: str) -> list[dict[str, object]]:
    return load_json(ROOT / "compat" / "corpus" / api / case_id / "expected.json")["result"]["records"]


def test_encode_transaction_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_transaction(1, "600036", 0, 10) == _request("transaction", "live_sh_600036_last10", "01_transaction")


def test_decode_transaction_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_transaction(_body("transaction", "live_sh_600036_last10", "01_transaction")) == _expected(
        "transaction", "live_sh_600036_last10"
    )


def test_encode_history_transactions_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_history_transactions(1, "600036", 0, 10, "20170209") == _request(
        "transactions", "history_sh_600036_20170209_last10", "01_transactions"
    )


def test_decode_history_transactions_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.decode_history_transactions(
        _body("transactions", "history_sh_600036_20170209_last10", "01_transactions")
    ) == _expected("transactions", "history_sh_600036_20170209_last10")


@pytest.mark.parametrize(
    ("decoder", "body"),
    [
        ("transaction", b""),
        ("transaction", b"\x01"),
        ("transaction", b"\x01\x00short"),
        ("transactions", b""),
        ("transactions", b"\x01"),
        ("transactions", b"\x01\x00short"),
    ],
)
def test_transaction_decoders_reject_invalid_bodies(decoder: str, body: bytes) -> None:
    protocol = StdQuoteProtocol()

    with pytest.raises(ProtocolDecodeError):
        if decoder == "transaction":
            protocol.decode_transaction(body)
        else:
            protocol.decode_history_transactions(body)
