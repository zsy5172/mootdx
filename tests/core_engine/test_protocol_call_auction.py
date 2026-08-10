from __future__ import annotations

import struct

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


CAPTURED_REQUEST_SZ000001 = bytes.fromhex(
    "0c00000000011e001e006a05000030303030303100000000030000000000000000000000f4010000"
)


def _body(*rows: tuple[int, float, int, int, int]) -> bytes:
    body = bytearray(struct.pack("<H", len(rows)))
    for raw_time, price, matched, unmatched, second in rows:
        body.extend(struct.pack("<HfIiBB", raw_time, price, matched, unmatched, 0, second))
    return bytes(body)


def test_encode_call_auction_matches_live_request() -> None:
    assert StdQuoteProtocol().encode_call_auction(0, "000001") == CAPTURED_REQUEST_SZ000001


def test_decode_call_auction_uses_signed_32_bit_unmatched_volume() -> None:
    rows = StdQuoteProtocol().decode_call_auction(
        _body(
            (9 * 60 + 15, 11.14, 324, 177, 0),
            (14 * 60 + 59, 11.28, 9138, -8869, 51),
        )
    )

    assert rows == [
        {
            "time": "09:15:00",
            "hour": 9,
            "minute": 15,
            "second": 0,
            "price": 11.14,
            "matched": 324,
            "unmatched_signed": 177,
            "unmatched": 177,
            "side": 1,
            "side_name": "buy",
        },
        {
            "time": "14:59:51",
            "hour": 14,
            "minute": 59,
            "second": 51,
            "price": 11.28,
            "matched": 9138,
            "unmatched_signed": -8869,
            "unmatched": 8869,
            "side": -1,
            "side_name": "sell",
        },
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x01",
        struct.pack("<H", 1),
        _body((24 * 60, 1.0, 1, 1, 0)),
        _body((9 * 60 + 15, 1.0, 1, 1, 60)),
    ],
)
def test_decode_call_auction_rejects_malformed_rows(body: bytes) -> None:
    with pytest.raises(ProtocolDecodeError):
        StdQuoteProtocol().decode_call_auction(body)


def test_sync_client_call_auction_routes_symbol_and_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_body((9 * 60 + 15, 11.14, 324, 177, 0))])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.call_auction("sz000001")

    assert rows[0]["time"] == "09:15:00"
    assert transport.sent_payloads == [CAPTURED_REQUEST_SZ000001]
