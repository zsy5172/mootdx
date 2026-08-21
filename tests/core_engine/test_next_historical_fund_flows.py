from __future__ import annotations

import struct

from mootdx_next.api.clients import SyncClient
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


def test_historical_fund_flow_request_matches_category_22_wire_shape() -> None:
    payload = StdQuoteProtocol().encode_historical_fund_flows(1, "600036", 0, 10)

    assert payload.hex() == (
        "0c01086401011c001c002d05010036303030333616000100"
        "00000a0000000000000000000000"
    )


def test_historical_fund_flow_decoder_preserves_native_fields() -> None:
    body = b"\x00" * 9 + struct.pack("<H9I", 1, 20260820, *([0] * 8))

    rows = StdQuoteProtocol().decode_historical_fund_flows(body)

    assert rows == [
        {
            "date": "2026-08-20",
            "year": 2026,
            "month": 8,
            "day": 20,
            "super_in": 0.0,
            "large_in": 0.0,
            "medium_in": 0.0,
            "small_in": 0.0,
            "super_out": 0.0,
            "large_out": 0.0,
            "medium_out": 0.0,
            "small_out": 0.0,
        }
    ]


def test_historical_fund_flows_adds_identity_and_source_metadata() -> None:
    body = b"\x00" * 9 + struct.pack("<H9I", 1, 20260820, *([0] * 8))
    transport = RecordingTransport(responses=[body])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(
        protocol=StdQuoteProtocol(),
        connection_pool=pool,
        scheduler=scheduler,
        max_retries=0,
    )

    rows = client.historical_fund_flows("sh600036", count=10)

    assert rows[0]["market"] == 1
    assert rows[0]["code"] == "600036"
    assert rows[0]["source"] == "tdx_category_22"
    assert transport.contexts[0].api == "historical_fund_flows"
