from __future__ import annotations

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol import StdQuoteProtocol
from mootdx_next.scheduler.pools import ServerPool
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


class BlockFundProtocol(StdQuoteProtocol):
    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        if api != "block_funds":
            return super().decode(api, envelope, **kwargs)
        return [
            {
                "market": 1,
                "code": "880550",
                "amount": 343_259_316_224.0,
                "main_buy_amount": 867_368_960.0,
                "main_net_amount": 25_795_477_504.0,
                "main_net_amount_5min": 744_428_573.1635201,
                "fund_amount_base": 341_480_996_864.0,
            }
        ]


class SwitchingBlockFundProtocol(BlockFundProtocol):
    def __init__(self) -> None:
        super().__init__()
        self.decode_count = 0

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        rows = super().decode(api, envelope, **kwargs)
        if api == "block_funds":
            self.decode_count += 1
            if self.decode_count == 1:
                rows[0]["fund_amount_base"] = 0.0  # type: ignore[index]
        return rows


def test_block_funds_joins_catalog_and_base_snapshot_for_capital_ratios() -> None:
    transport = RecordingTransport(responses=[b"decoded-by-test-protocol"])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=BlockFundProtocol(), connection_pool=pool, scheduler=scheduler)
    client._resolve_block_fund_entries = lambda symbol, category, refresh: [  # type: ignore[method-assign]
        {
            "name": "PCB概念",
            "code": "880550",
            "category": "concept",
            "category_name": "概念板块",
            "taxonomy": None,
            "source": "tdxzs3.cfg",
        }
    ]
    client.tdx_block_base = lambda refresh=False: [  # type: ignore[method-assign]
        {
            "market": 1,
            "code": "880550",
            "date": "20260807",
            "total_market_cap": 6_497_430_319_601.0,
            "circulating_market_cap": 5_529_032_050_832.0,
        }
    ]

    row = client.block_funds("880550")[0]

    assert row["name"] == "PCB概念"
    assert row["catalog_source"] == "tdxzs3.cfg"
    assert row["base_date"] == "20260807"
    assert row["net_buy_rate"] == pytest.approx(0.0156875372)
    assert row["main_force_net_ratio"] == pytest.approx(0.4665459933)
    assert transport.sent_payloads[0] == client.protocol.encode_block_funds([(1, "880550")])


def test_explicit_typed_block_does_not_load_constituent_index_catalog() -> None:
    client = SyncClient()
    client._typed_block_catalog = lambda refresh=False: [  # type: ignore[method-assign]
        {
            "name": "PCB概念",
            "code": "880550",
            "category": "concept",
            "category_name": "概念板块",
            "taxonomy": None,
            "source": "tdxzs3.cfg",
        }
    ]

    def fail_if_loaded(refresh=False):
        raise AssertionError("the index catalog should not be loaded")

    client._index_block_catalog = fail_if_loaded  # type: ignore[method-assign]

    assert client._resolve_block_fund_entries("880550", None, False)[0]["name"] == "PCB概念"
    assert client._resolve_block_fund_entries("PCB概念", "概念", False)[0]["code"] == "880550"

    with pytest.raises(ValueError, match="unknown block"):
        client._resolve_block_fund_entries("880550", "行业", False)


def test_zeroed_fund_extension_is_returned_as_unavailable_data() -> None:
    transport = RecordingTransport(responses=[b"zero-extension", b"fund-extension"])
    pool = RecordingConnectionPool(transport)
    servers = [
        ServerEndpoint(host="182.140.139.191", port=7709, label="funds-1"),
        ServerEndpoint(host="119.6.200.40", port=7709, label="funds-2"),
    ]
    scheduler = ServerPool(servers=servers, connection_pool=pool)
    protocol = SwitchingBlockFundProtocol()
    client = SyncClient(
        protocol=protocol,
        connection_pool=pool,
        scheduler=scheduler,
        max_retries=1,
    )
    client._resolve_block_fund_entries = lambda symbol, category, refresh: [  # type: ignore[method-assign]
        {"name": "PCB概念", "code": "880550"}
    ]
    client.tdx_block_base = lambda refresh=False: [  # type: ignore[method-assign]
        {
            "market": 1,
            "code": "880550",
            "circulating_market_cap": 5_529_032_050_832.0,
        }
    ]

    row = client.block_funds("880550")[0]

    assert row["fund_amount_base"] == 0.0
    assert row["fund_extension_available"] is False
    assert row["fund_extension_status"] == "unavailable"
    assert protocol.decode_count == 1
    assert len(transport.sent_payloads) == 1
    assert pool.discarded == []
    assert pool.released[0].server == servers[0]


def test_block_fund_rankings_keep_missing_values_last_in_both_directions() -> None:
    rows = [
        {"code": "1", "value": 2.0},
        {"code": "2", "value": None},
        {"code": "3", "value": -1.0},
    ]

    assert [row["code"] for row in SyncClient._sort_block_funds(rows, "value", True)] == ["1", "3", "2"]
    assert [row["code"] for row in SyncClient._sort_block_funds(rows, "value", False)] == ["3", "1", "2"]

    with pytest.raises(ValueError, match="unknown block fund sort field"):
        SyncClient._sort_block_funds(rows, "missing", True)
