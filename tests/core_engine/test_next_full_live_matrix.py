from __future__ import annotations

import asyncio
import os
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone

import pandas as pd
import pytest

from mootdx.consts import HQ_HOSTS
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import Quotes
from mootdx_next import AsyncClient
from mootdx_next import CAPABILITY_FUND_FLOWS
from mootdx_next import MacFieldPreset
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from mootdx_next.analytics import forward_returns
from mootdx_next.analytics import ma
from mootdx_next.constants import FUND_FLOW_HOSTS
from mootdx_next.constants import MAC_EX_HOSTS
from mootdx_next.constants import MAC_HOSTS
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.session import is_trading_session


pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_LIVE_MATRIX") != "1",
    reason="set MOOTDX_RUN_LIVE_MATRIX=1 to run the exhaustive live matrix",
)


def _servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in HQ_HOSTS[:5]]


def _fund_flow_servers() -> list[ServerEndpoint]:
    generic_label, generic_host, generic_port = next(
        item for item in HQ_HOSTS if (item[1], item[2]) not in FUND_FLOW_HOSTS
    )
    generic = ServerEndpoint(
        host=generic_host,
        port=generic_port,
        label=f"generic:{generic_label}",
    )
    supplemental = [
        ServerEndpoint(
            host=host,
            port=port,
            label=f"fund-flow:{index}",
            capabilities=frozenset({CAPABILITY_FUND_FLOWS}),
        )
        for index, (host, port) in enumerate(sorted(FUND_FLOW_HOSTS), 1)
    ]
    return [generic, *supplemental]


def _is_weekday_trading_session() -> bool:
    now = datetime.now()
    return now.weekday() < 5 and is_trading_session(now)


def _is_weekday_auction_fund_window() -> bool:
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.weekday() < 5 and time(9, 25) <= now.time() < time(9, 30)


@pytest.fixture(scope="module")
def live_client():
    client = SyncClient(servers=_servers(), max_retries=2)
    yield client
    client.close()


@pytest.fixture(scope="module")
def live_fund_flow_client():
    servers = _fund_flow_servers()
    client = SyncClient(servers=servers, max_retries=len(servers) - 2)
    yield client
    client.close()


def _mac_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in MAC_HOSTS]


def _mac_ex_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in MAC_EX_HOSTS]


@pytest.fixture(scope="module")
def live_mac_client():
    client = SyncClient(mac_servers=_mac_servers(), max_retries=2)
    yield client
    client.close()


@pytest.fixture(scope="module")
def live_mac_ex_client():
    from mootdx_next import ExSyncClient

    client = ExSyncClient(mac_servers=_mac_ex_servers(), max_retries=1)
    yield client
    client.close()


def test_live_mac_a_capability_matrix(live_mac_client: SyncClient) -> None:
    quotes = live_mac_client.mac_quotes("sh600519", fields=MacFieldPreset.BASIC)
    boards = live_mac_client.mac_board_list(count=2)
    members = live_mac_client.mac_board_members("881001", count=2, fields=MacFieldPreset.FUND_FLOW)
    capital = live_mac_client.mac_capital_flow("sh600519")
    server_info = live_mac_client.mac_server_info()

    assert quotes and {"market", "code", "name", "close", "vol"} <= set(quotes[0])
    assert len(boards) <= 2
    assert members and "main_net_5m_amount" in members[0]
    assert capital and {"main_in", "main_out", "main_net"} <= set(capital[0])
    assert server_info and server_info[0]["today"]
    assert server_info[0]["sessions_1"][:2] == [
        {"open": "9:30", "close": "11:30"},
        {"open": "13:00", "close": "15:00"},
    ]

    paged_members = live_mac_client.mac_board_members(
        "880001", count=90, fields=MacFieldPreset.BASIC
    )
    assert len(paged_members) == 90
    assert len({(row.get("market"), row.get("code")) for row in paged_members}) == 90


def test_live_mac_ex_login_and_quote_matrix(live_mac_ex_client) -> None:
    rows = live_mac_ex_client.mac_quotes([(31, "00700")], fields=MacFieldPreset.BASIC)
    bars = live_mac_ex_client.mac_bars(31, "00700", count=2)
    ticks = live_mac_ex_client.mac_tick_chart(31, "00700")
    transactions = live_mac_ex_client.mac_transactions(31, "00700", count=2)

    assert rows and rows[0]["code"] == "00700"
    assert bars and len(bars) <= 2
    assert isinstance(ticks, list)
    # The HK compatibility route legitimately returns no rows before the
    # first trade of the session (and on holidays).  The capability check is
    # therefore about shape, not whether the market currently has prints.
    assert isinstance(transactions, list)
    assert len(transactions) <= 2

    listed = live_mac_ex_client.mac_quotes_list(
        31, count=3, fields=MacFieldPreset.BASIC
    )
    assert len(listed) == 3
    assert all(row.get("code") for row in listed)

    paged_transactions = live_mac_ex_client.mac_transactions(31, "00700", count=1801)
    assert isinstance(paged_transactions, list)
    assert len(paged_transactions) <= 1801


def test_live_native_historical_fund_flows(live_client: SyncClient) -> None:
    rows = live_client.historical_fund_flows("sh600036", count=3)

    assert isinstance(rows, list)
    if rows:
        assert rows[0]["source"] == "tdx_category_22"
        assert {"super_in", "large_in", "medium_in", "small_in"} <= set(rows[0])


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


def test_live_limit_price_server_table_and_rule_fallback(live_client: SyncClient) -> None:
    rows = live_client.limit_prices()
    assert rows
    assert all({"market", "code", "limit_up", "limit_down"} <= set(row) for row in rows)

    special = next(row for row in rows if row["market"] in {0, 1})
    prefix = "sz" if special["market"] == 0 else "sh"
    resolved = live_client.price_limit(f"{prefix}{special['code']}", refresh=True)
    assert resolved == {**special, "source": "server"}

    ordinary = live_client.price_limit("600036")
    assert ordinary is not None
    assert ordinary["source"] in {"server", "calculated"}
    assert ordinary["limit_up"] > ordinary["limit_down"]


def test_live_fund_flows_routes_only_to_capable_supplemental_server(
    live_fund_flow_client: SyncClient,
) -> None:
    rows = live_fund_flow_client.fund_flows(["600036", "880550"])

    assert {row["code"] for row in rows} == {"600036", "880550"}
    assert all(row["quote_source"] == "tdx_0x054c_mode1" for row in rows)

    snapshots = live_fund_flow_client.scheduler.snapshot()
    generic = next(item for item in snapshots if item.server.label.startswith("generic:"))
    used = [item for item in snapshots if item.success_count > 0]

    assert generic.success_count == 0
    assert generic.failure_count == 0
    assert generic.last_latency_ms is None
    assert used
    assert all(CAPABILITY_FUND_FLOWS in item.server.capabilities for item in used)


def test_live_fund_flows_zero_extension_is_a_successful_response(
    live_fund_flow_client: SyncClient,
) -> None:
    symbol = os.getenv("MOOTDX_LIVE_ZERO_FUND_SYMBOL", "bj920001")
    before = live_fund_flow_client.scheduler.snapshot()
    success_count = sum(item.success_count for item in before)
    failure_count = sum(item.failure_count for item in before)

    rows = live_fund_flow_client.fund_flows(symbol)

    assert len(rows) == 1
    row = rows[0]
    assert row["fund_amount_base"] == 0.0
    assert row["fund_extension_available"] is False
    assert row["fund_extension_status"] == "unavailable"
    assert row["quote_source"] == "tdx_0x054c_mode1"

    after = live_fund_flow_client.scheduler.snapshot()
    assert sum(item.success_count for item in after) == success_count + 1
    assert sum(item.failure_count for item in after) == failure_count


@pytest.mark.skipif(
    not _is_weekday_auction_fund_window(),
    reason="竞价零扩展仅在工作日 09:25-09:30 验证",
)
def test_live_fund_flows_auction_zero_extension_does_not_fail_node(
    live_fund_flow_client: SyncClient,
) -> None:
    before = live_fund_flow_client.scheduler.snapshot()
    success_count = sum(item.success_count for item in before)
    failure_count = sum(item.failure_count for item in before)

    rows = live_fund_flow_client.fund_flows("880550")

    assert rows
    assert all(row["fund_extension_available"] is False for row in rows)
    assert all(row["fund_extension_status"] == "unavailable" for row in rows)
    after = live_fund_flow_client.scheduler.snapshot()
    assert sum(item.success_count for item in after) == success_count + 1
    assert sum(item.failure_count for item in after) == failure_count


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
    xdxr = live_client.xdxr("600036")
    assert isinstance(xdxr, list)

    grouped = live_client.xdxr_by_date("600036")
    assert sum(len(rows) for rows in grouped.values()) == len(xdxr)

    latest = live_client.bars("600036", frequency=9, start=0, offset=1)[0]
    market_value = live_client.market_value(
        "600036",
        str(latest["datetime"])[:10],
        float(latest["close"]),
    )
    assert market_value is not None
    assert market_value["float_market_value"] > 0
    assert market_value["total_market_value"] >= market_value["float_market_value"]

    categories = live_client.f10_categories("600036")
    assert isinstance(categories, list)
    if categories:
        assert isinstance(live_client.f10_content("600036", categories[0]["name"]), str)

    assert isinstance(live_client.block("block_zs.dat"), list)


def test_live_call_auction_and_public_config_ecosystem(live_client: SyncClient) -> None:
    auction = live_client.call_auction("600036")
    assert isinstance(auction, list)
    if auction:
        assert {"time", "price", "matched", "unmatched", "side", "side_name"} <= set(
            auction[0]
        )

    raw_block = live_client.block_file_raw("block_gn.dat")
    archive = live_client.report_file("zhb.zip")
    files = live_client.zhb_files(refresh=True)
    indexes = live_client.tdx_block_indexes()
    aliases = live_client.tdx_block_aliases()
    indexed_components = live_client.block_with_index("block_gn.dat")
    sp_blocks = live_client.sp_blocks()
    industries = live_client.tdx_industries()
    subscriptions = live_client.ipo_subscriptions()
    statistics = live_client.stock_statistics()
    statistics2 = live_client.stock_statistics2()

    assert len(raw_block) > 384
    assert archive.startswith(b"PK")
    assert "tdxzs.cfg" in files
    assert indexes and aliases and indexed_components
    assert sp_blocks and industries and subscriptions
    assert statistics and statistics2
    assert all("raw_fields" in row for row in statistics[:10])
    assert all("raw_fields" in row for row in statistics2[:10])


def test_live_analytics_and_lazy_history_iterator(live_client: SyncClient) -> None:
    daily = pd.DataFrame.from_records(
        live_client.bars("600036", frequency=9, start=0, offset=30)
    )
    assert ma(daily, 5).iloc[-1] == pytest.approx(daily["close"].tail(5).mean())
    returns = forward_returns(daily, horizons=(1, 5))
    assert returns.iloc[0]["return_5"] == pytest.approx(
        daily.iloc[5]["close"] / daily.iloc[0]["close"] - 1
    )

    historical_date, rows = next(
        live_client.iter_transaction_history(
            "600036",
            before="20020531",
            page_size=2000,
            max_pages=1,
        )
    )
    assert historical_date <= "20020531"
    assert rows


def test_live_async_matrix() -> None:
    async def run() -> None:
        client = AsyncClient(servers=_servers(), max_retries=2)
        try:
            (
                quotes,
                limits,
                price_limit,
                bars,
                finance,
                index,
                categories,
                block,
                grouped_xdxr,
                market_value,
            ) = await asyncio.gather(
                client.quotes(["600036", "000001"]),
                client.limit_prices(0, 10),
                client.price_limit("600036"),
                client.bars("600036", "day", 0, 2),
                client.finance("600036"),
                client.index_bars("000001", "day", 0, 2, 1),
                client.f10_categories("600036"),
                client.block("block_zs.dat"),
                client.xdxr_by_date("600036"),
                client.market_value("600036", "20200102", 37.0),
            )
            assert isinstance(quotes, list)
            assert isinstance(limits, list) and limits
            assert isinstance(price_limit, dict)
            assert isinstance(bars, list)
            assert isinstance(finance, dict)
            assert isinstance(index, list)
            assert isinstance(categories, list)
            assert isinstance(block, list)
            assert grouped_xdxr
            assert market_value is not None
            if categories:
                assert isinstance(await client.f10_content("600036", categories[0]["name"]), str)
        finally:
            client.close()

    asyncio.run(run())


def test_live_legacy_compatible_facade_matrix() -> None:
    client = Quotes.factory(engine="next")
    try:
        quotes = client.quotes("600036")
        limits = client.limit_prices(count=10)
        price_limit = client.price_limit("600036")
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
        assert isinstance(limits, pd.DataFrame) and not limits.empty
        assert isinstance(price_limit, pd.DataFrame) and len(price_limit) == 1
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
        latest_closes = []
        for frequency, offset in [(9, 30), (5, 30), (6, 30), (10, 30), (11, 20)]:
            adjusted = client.bars(
                "600036",
                frequency=frequency,
                start=0,
                offset=offset,
                adjust="qfq",
            )
            assert not adjusted.empty
            assert {"open", "high", "low", "close", "factor"} <= set(adjusted.columns)
            latest_closes.append(adjusted["close"].iloc[-1])
        assert latest_closes == pytest.approx(
            [latest_closes[0]] * len(latest_closes),
            rel=0.02,
        )

        with pytest.raises(MootdxValidationException, match="category 14.*2006-02-27.*估值"):
            client.bars("600036", frequency=11, offset=30, adjust="qfq")
        with pytest.raises(MootdxValidationException, match="category 14.*2006-02-27.*估值"):
            client.bars("600036", frequency=9, offset=30, adjust="hfq")

        for adjust in ["tdx_qfq", "tdx_hfq"]:
            latest_closes = []
            for frequency in [9, 5, 6, 10, 11]:
                adjusted = client.bars(
                    "600036",
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

        for symbol in ["510500"]:
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

        lof_qfq = client.get_k_data(
            "161725",
            start_date="2021-01-15",
            end_date="2026-07-28",
            adjust="qfq",
        )
        assert lof_qfq.iloc[0]["open"] == pytest.approx(1.384)
        assert lof_qfq.iloc[0]["close"] == pytest.approx(1.355)

        for symbol, expected_first_close in [
            ("508000", 2.69508),
            ("180101", 2.2342),
        ]:
            fund_qfq = client.get_k_data(
                symbol,
                start_date="2021-06-21",
                end_date="2026-07-28",
                adjust="qfq",
            )
            assert fund_qfq.iloc[0]["close"] == pytest.approx(expected_first_close)

        bj_qfq = client.get_k_data(
            "BJ920001",
            start_date="2025-12-04",
            end_date="2025-12-10",
            adjust="qfq",
        )
        bj_hfq = client.get_k_data(
            "BJ920001",
            start_date="2025-12-04",
            end_date="2025-12-10",
            adjust="hfq",
        )
        assert bj_qfq.loc["2025-12-05", "factor"] == pytest.approx(0.995042)
        assert bj_qfq.loc["2025-12-08", "factor"] == pytest.approx(1)
        assert bj_hfq.loc["2025-12-05", "factor"] == pytest.approx(1.04704)
        assert bj_hfq.loc["2025-12-08", "factor"] == pytest.approx(1.052257)
    finally:
        client.close()
