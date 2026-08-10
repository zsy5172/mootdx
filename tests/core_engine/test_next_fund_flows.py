from __future__ import annotations

from mootdx_next.api.clients import SyncClient
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.protocol import StdQuoteProtocol
from mootdx_next.scheduler.pools import ServerPool
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport


class FundFlowProtocol(StdQuoteProtocol):
    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        if api != "fund_flows":
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


class SwitchingFundFlowProtocol(FundFlowProtocol):
    def __init__(self) -> None:
        super().__init__()
        self.decode_count = 0

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        rows = super().decode(api, envelope, **kwargs)
        if api == "fund_flows":
            self.decode_count += 1
            if self.decode_count == 1:
                rows[0]["fund_amount_base"] = 0.0  # type: ignore[index]
        return rows


def test_fund_flows_queries_explicit_symbols_without_loading_block_metadata() -> None:
    transport = RecordingTransport(responses=[b"decoded-by-test-protocol"])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=FundFlowProtocol(), connection_pool=pool, scheduler=scheduler)

    def fail_if_loaded(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("fund_flows should not load block metadata")

    client.block_catalog = fail_if_loaded  # type: ignore[method-assign]
    client.tdx_block_base = fail_if_loaded  # type: ignore[method-assign]

    row = client.fund_flows(["sh600036", "880550"])[0]

    assert row["code"] == "880550"
    assert row["main_net_amount"] == 25_795_477_504.0
    assert "name" not in row
    assert "main_force_net_ratio" not in row
    assert "net_buy_rate" not in row
    assert transport.sent_payloads[0] == client.protocol.encode_fund_flows(
        [(1, "600036"), (1, "880550")]
    )


def test_fund_flows_returns_empty_without_sending_when_symbols_are_empty() -> None:
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=FundFlowProtocol(), connection_pool=pool, scheduler=scheduler)

    assert client.fund_flows() == []
    assert client.fund_flows([]) == []
    assert transport.sent_payloads == []


def test_fund_flows_pages_explicit_symbols_in_batches_of_80() -> None:
    transport = RecordingTransport(responses=[b"page-1", b"page-2"])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=FundFlowProtocol(), connection_pool=pool, scheduler=scheduler)
    symbols = [f"60{index:04d}" for index in range(81)]

    rows = client.fund_flows(symbols)

    assert len(rows) == 2
    assert transport.sent_payloads[0] == client.protocol.encode_fund_flows(
        [(1, code) for code in symbols[:80]]
    )
    assert transport.sent_payloads[1] == client.protocol.encode_fund_flows([(1, symbols[80])])


def test_zeroed_fund_extension_retries_on_another_supplemental_server() -> None:
    transport = RecordingTransport(responses=[b"zero-extension", b"fund-extension"])
    pool = RecordingConnectionPool(transport)
    servers = [
        ServerEndpoint(host="182.140.139.191", port=7709, label="funds-1"),
        ServerEndpoint(host="119.6.200.40", port=7709, label="funds-2"),
    ]
    scheduler = ServerPool(servers=servers, connection_pool=pool)
    protocol = SwitchingFundFlowProtocol()
    client = SyncClient(
        protocol=protocol,
        connection_pool=pool,
        scheduler=scheduler,
        max_retries=1,
    )
    row = client.fund_flows("880550")[0]

    assert row["fund_amount_base"] == 341_480_996_864.0
    assert protocol.decode_count == 2
    assert len(transport.sent_payloads) == 2
    assert pool.discarded[0].server == servers[0]
    assert pool.released[0].server == servers[1]
