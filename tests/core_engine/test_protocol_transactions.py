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
    expected = _expected("transaction", "live_sh_600036_last10")
    actual = protocol.decode_transaction(_body("transaction", "live_sh_600036_last10", "01_transaction"))

    assert [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)] == expected
    assert actual[0]["side_name"] == "buy"
    assert actual[0]["is_buy"] is True
    assert actual[0]["is_sell"] is False
    assert actual[0]["amount"] == pytest.approx(39.21 * 55 * 100)
    assert actual[0]["average_volume"] == pytest.approx(55 / 28)


def test_encode_history_transactions_matches_corpus_request() -> None:
    protocol = StdQuoteProtocol()

    assert protocol.encode_history_transactions(1, "600036", 0, 10, "20170209") == _request(
        "transactions", "history_sh_600036_20170209_last10", "01_transactions"
    )


def test_decode_history_transactions_matches_corpus_expected() -> None:
    protocol = StdQuoteProtocol()
    expected = _expected("transactions", "history_sh_600036_20170209_last10")
    actual = protocol.decode_history_transactions(
        _body("transactions", "history_sh_600036_20170209_last10", "01_transactions")
    )

    assert [{key: row[key] for key in legacy} for row, legacy in zip(actual, expected, strict=True)] == expected


def test_transaction_decoders_use_etf_price_scale_and_history_date_context() -> None:
    protocol = StdQuoteProtocol()
    current = protocol.decode_transaction(
        _body("transaction", "live_sh_600036_last10", "01_transaction"),
        market=1,
        code="510300",
    )
    history = protocol.decode_history_transactions(
        _body("transactions", "history_sh_600036_20170209_last10", "01_transactions"),
        market=1,
        code="510300",
        date="20170209",
    )

    assert current[0]["price"] == pytest.approx(3.921)
    assert "datetime" not in current[0]
    assert history[0]["price"] == pytest.approx(1.873)
    assert history[0]["datetime"] == "2017-02-09 14:59"


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
