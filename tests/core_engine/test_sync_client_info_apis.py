from __future__ import annotations

from pathlib import Path

import pytest

from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.protocol import StdQuoteProtocol
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler
from tests.core_engine.test_sync_client_stock_apis import RecordingTransport

ROOT = Path(__file__).resolve().parents[2]


def _body(api: str, case_id: str, step_id: str) -> bytes:
    return (ROOT / "compat" / "corpus" / api / case_id / "steps" / step_id / "response.body.bin").read_bytes()


def test_sync_client_finance_decodes_row() -> None:
    transport = RecordingTransport(responses=[_body("finance", "sz_000001", "01_finance")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    row = client.finance(symbol="000001")

    assert row["code"] == "000001"
    assert "liutongguben" in row


def test_sync_client_xdxr_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_body("xdxr", "sh_600036", "01_xdxr")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.xdxr(symbol="600036")

    assert rows
    assert {"year", "month", "day", "category", "name"} <= set(rows[0])


def test_sync_client_f10_categories_decodes_rows() -> None:
    transport = RecordingTransport(responses=[_body("f10_categories", "sh_600036", "01_f10_categories")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    rows = client.f10_categories(symbol="600036")

    assert len(rows) == 16
    assert rows[0]["name"] == "最新提示"


def test_sync_client_f10_content_reads_named_category() -> None:
    transport = RecordingTransport(
        responses=[
            _body("f10_categories", "sh_600036", "01_f10_categories"),
            _body("f10_content", "sh_600036__latest_tip", "02_f10_content"),
        ]
    )
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    content = client.f10_content(symbol="600036", name="最新提示")

    assert content.startswith("最新提示")


def test_sync_client_info_apis_reject_invalid_inputs() -> None:
    client = SyncClient()

    with pytest.raises(InvalidSymbolError):
        client.finance(symbol="")
    with pytest.raises(InvalidSymbolError):
        client.xdxr(symbol="")
    with pytest.raises(InvalidSymbolError):
        client.f10_categories(symbol="")
    with pytest.raises(UnknownF10CategoryError):
        client.f10_content(symbol="600036", name="")


def test_sync_client_info_apis_reject_bj() -> None:
    client = SyncClient()

    with pytest.raises(UnsupportedMarketError):
        client.finance(symbol="430090")
    with pytest.raises(UnsupportedMarketError):
        client.xdxr(symbol="430090")
    with pytest.raises(UnsupportedMarketError):
        client.f10_categories(symbol="430090")
    with pytest.raises(UnsupportedMarketError):
        client.f10_content(symbol="430090", name="最新提示")


def test_sync_client_f10_content_raises_for_unknown_category() -> None:
    transport = RecordingTransport(responses=[_body("f10_categories", "sh_600036", "01_f10_categories")])
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(UnknownF10CategoryError):
        client.f10_content(symbol="600036", name="不存在的栏目")


def test_sync_client_info_apis_propagate_scheduler_failure() -> None:
    transport = RecordingTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server, select_error=NoHealthyServerError("no server"))
    client = SyncClient(protocol=StdQuoteProtocol(), connection_pool=pool, scheduler=scheduler)

    with pytest.raises(NoHealthyServerError):
        client.finance(symbol="000001")

    with pytest.raises(NoHealthyServerError):
        client.xdxr(symbol="600036")
