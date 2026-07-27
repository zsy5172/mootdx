from __future__ import annotations

import asyncio
import os
from datetime import datetime

import pandas as pd
import pytest

from mootdx.consts import HQ_HOSTS
from mootdx.quotes import Quotes
from mootdx_next import AsyncClient
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.session import is_trading_session


pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_LIVE_MATRIX") != "1",
    reason="set MOOTDX_RUN_LIVE_MATRIX=1 to run the exhaustive live matrix",
)


def _servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in HQ_HOSTS[:5]]


def _is_weekday_trading_session() -> bool:
    now = datetime.now()
    return now.weekday() < 5 and is_trading_session(now)


@pytest.fixture(scope="module")
def live_client():
    client = SyncClient(servers=_servers(), max_retries=2)
    yield client
    client.close()


@pytest.mark.parametrize("market", [0, 1, 2])
def test_live_stock_count_market_matrix(live_client: SyncClient, market: int) -> None:
    assert live_client.stock_count(market) >= 0


@pytest.mark.parametrize("market", [0, 1])
def test_live_stock_list_market_matrix(live_client: SyncClient, market: int) -> None:
    assert isinstance(live_client.stocks(market), list)


@pytest.mark.parametrize(
    "symbols",
    ["600036", "sz000001", "bj430090", ["600036", "000001"], ["sh000001", "sz000001", "600036"]],
)
def test_live_quote_symbol_matrix(live_client: SyncClient, symbols) -> None:
    assert isinstance(live_client.quotes(symbols), list)


@pytest.mark.parametrize("frequency", list(range(12)))
def test_live_bar_frequency_matrix(live_client: SyncClient, frequency: int) -> None:
    assert isinstance(live_client.bars("600036", frequency=frequency, start=0, offset=2), list)


@pytest.mark.parametrize(
    ("symbol", "market"),
    [("000001", None), ("399001", None), ("000001", 1), ("399001", 0)],
)
def test_live_index_market_matrix(live_client: SyncClient, symbol: str, market: int | None) -> None:
    assert isinstance(live_client.index_bars(symbol, frequency=9, offset=2, market=market), list)


def test_live_index_rejects_stock_payload_from_mismatched_market(live_client: SyncClient) -> None:
    with pytest.raises(ProtocolDecodeError):
        live_client.index_bars("000001", frequency=9, offset=2, market=0)


@pytest.mark.parametrize("date", ["20171010", 20171010, "2017-10-10"])
def test_live_minute_date_matrix(live_client: SyncClient, date: str | int) -> None:
    assert isinstance(live_client.minutes("000001", date), list)


@pytest.mark.skipif(
    not _is_weekday_trading_session(), reason="实时逐笔成交仅在工作日交易时段验证"
)
def test_live_transaction_session_matrix(live_client: SyncClient) -> None:
    rows = live_client.transaction("600036", start=0, offset=2)
    assert isinstance(rows, list)


@pytest.mark.parametrize(("date", "start", "offset"), [("20170209", 0, 1), (20170209, 20, 15)])
def test_live_historical_transaction_window_matrix(
    live_client: SyncClient,
    date: str | int,
    start: int,
    offset: int,
) -> None:
    assert isinstance(live_client.transactions("600036", date, start=start, offset=offset), list)


def test_live_information_and_block_matrix(live_client: SyncClient) -> None:
    assert isinstance(live_client.finance("600036"), dict)
    assert isinstance(live_client.xdxr("600036"), list)

    categories = live_client.f10_categories("600036")
    assert isinstance(categories, list)
    if categories:
        assert isinstance(live_client.f10_content("600036", categories[0]["name"]), str)

    assert isinstance(live_client.block("block_zs.dat"), list)


def test_live_async_matrix() -> None:
    async def run() -> None:
        client = AsyncClient(servers=_servers(), max_retries=2)
        try:
            quotes, bars, finance, index, categories, block = await asyncio.gather(
                client.quotes(["600036", "000001"]),
                client.bars("600036", "day", 0, 2),
                client.finance("600036"),
                client.index_bars("000001", "day", 0, 2, 1),
                client.f10_categories("600036"),
                client.block("block_zs.dat"),
            )
            assert isinstance(quotes, list)
            assert isinstance(bars, list)
            assert isinstance(finance, dict)
            assert isinstance(index, list)
            assert isinstance(categories, list)
            assert isinstance(block, list)
            if categories:
                assert isinstance(await client.f10_content("600036", categories[0]["name"]), str)
        finally:
            client.close()

    asyncio.run(run())


def test_live_legacy_compatible_facade_matrix() -> None:
    client = Quotes.factory(engine="next")
    try:
        quotes = client.quotes("600036")
        bars = client.bars("600036", frequency="day", offset=2)
        index = client.index("000001", frequency="day", offset=2)
        finance = client.finance("600036")
        get_k_data = client.get_k_data(
            "600036",
            start_date="2026-07-20",
            end_date="2026-07-25",
        )
        k_data = client.k("600036", begin="2026-07-20", end="2026-07-25")
        ohlc = client.ohlc(symbol="600036", begin="2026-07-20", end="2026-07-25")

        assert isinstance(quotes, pd.DataFrame) and not quotes.empty
        assert isinstance(bars, pd.DataFrame) and not bars.empty
        assert isinstance(index, pd.DataFrame) and not index.empty
        assert isinstance(finance, pd.DataFrame) and not finance.empty
        assert client.F10C("600036")
        assert isinstance(get_k_data, pd.DataFrame) and len(get_k_data) == 5
        assert isinstance(k_data, pd.DataFrame) and len(k_data) == 5
        assert isinstance(ohlc, pd.DataFrame) and len(ohlc) == 5
        assert "volume" not in get_k_data.columns
        assert "volume" in k_data.columns
        assert "volume" in ohlc.columns
    finally:
        client.close()


def test_live_real_adjustment_and_history_wrapper_matrix() -> None:
    client = Quotes.factory(engine="next", servers=_servers(), timeout=5)
    try:
        for symbol in ["600036", "510500"]:
            for adjust in ["qfq", "hfq"]:
                latest_closes = []
                for frequency in [9, 5, 6, 10, 11]:
                    adjusted = client.bars(
                        symbol,
                        frequency=frequency,
                        start=0,
                        offset=30,
                        adjust=adjust,
                    )
                    assert not adjusted.empty
                    assert {"open", "high", "low", "close", "factor"} <= set(
                        adjusted.columns
                    )
                    latest_closes.append(adjusted["close"].iloc[-1])
                assert latest_closes == pytest.approx(
                    [latest_closes[0]] * len(latest_closes),
                    rel=0.02,
                )

        for adjust in ["qfq", "hfq"]:
            get_k_data = client.get_k_data(
                "510500",
                start_date="2026-07-08",
                end_date="2026-07-17",
                adjust=adjust,
            )
            k_data = client.k(
                "510500",
                begin="2026-07-08",
                end="2026-07-17",
                adjust=adjust,
            )
            ohlc = client.ohlc(
                symbol="510500",
                begin="2026-07-08",
                end="2026-07-17",
                adjust=adjust,
            )
            assert len(get_k_data) == len(k_data) == len(ohlc) == 8
            assert "volume" not in get_k_data.columns
            assert "volume" in k_data.columns
            assert "volume" in ohlc.columns
            assert "factor" in get_k_data.columns
    finally:
        client.close()
