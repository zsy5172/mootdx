from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import date

import pytest

from mootdx_next import AsyncClient
from mootdx_next import PandasClient
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS


pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("MOOTDX_RUN_UPSTREAM_GAPS_LIVE") != "1",
        reason="set MOOTDX_RUN_UPSTREAM_GAPS_LIVE=1 to verify injoyai/tdx gap fixes",
    ),
]

NEW_SH_ETFS = ("520500", "530000", "560010", "588000")


def _servers() -> list[ServerEndpoint]:
    return [
        ServerEndpoint(host=host, port=port, label=label)
        for label, host, port in PREFERRED_HQ_HOSTS[:5]
    ]


def _completed_bar(rows: list[dict[str, object]]) -> tuple[str, dict[str, object]]:
    today = date.today().isoformat()
    for row in reversed(rows):
        day = str(row["datetime"])[:10]
        if day < today:
            return day, row
    if len(rows) < 2:
        raise AssertionError("daily bars do not contain a completed trading day")
    return str(rows[-2]["datetime"])[:10], rows[-2]


def _complete_intraday_group(
    daily_rows: list[dict[str, object]],
    minute_rows: list[dict[str, object]],
) -> tuple[str, dict[str, object], list[dict[str, object]]]:
    daily = {str(row["datetime"])[:10]: row for row in daily_rows}
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in minute_rows:
        grouped[str(row["datetime"])[:10]].append(row)
    for day in sorted(grouped, reverse=True):
        if day in daily and len(grouped[day]) == 240:
            return day, daily[day], grouped[day]
    raise AssertionError("live bars do not contain a complete 240-minute trading day")


@pytest.fixture(scope="module")
def live_client() -> SyncClient:
    client = SyncClient(servers=_servers(), max_retries=2)
    yield client
    client.close()


def test_live_security_directories_cover_new_etfs_and_bse(live_client: SyncClient) -> None:
    stocks = live_client.stock_codes()
    etfs = live_client.etf_codes()
    indexes = live_client.index_codes()
    bse = live_client.stocks(2)

    assert "sh600036" in stocks
    assert "sh000001" in indexes
    assert all(f"sh{symbol}" in etfs for symbol in NEW_SH_ETFS)
    assert all(any(symbol.startswith(f"sh{prefix}") for symbol in etfs) for prefix in ("52", "53", "56", "58"))
    assert bse
    assert all(row["market"] == 2 and row["source"] == "bse" for row in bse)

    security = live_client.security("588000")
    assert security is not None
    assert security["security_type"] == "etf"
    assert security["decimal_point"] == 3


@pytest.mark.parametrize("symbol", NEW_SH_ETFS)
def test_live_new_sh_etf_quote_and_minute_precision(
    live_client: SyncClient,
    symbol: str,
) -> None:
    daily = live_client.bars(symbol, frequency=9, start=0, offset=10)
    assert daily
    completed_day, completed = _completed_bar(daily)

    quote = live_client.quotes(symbol)
    minutes = live_client.minutes(symbol, completed_day.replace("-", ""))
    assert len(quote) == 1
    assert minutes
    assert float(daily[-1]["low"]) - 0.001 <= float(quote[0]["price"]) <= float(daily[-1]["high"]) + 0.001
    assert float(minutes[-1]["price"]) == pytest.approx(float(completed["close"]), abs=0.001)
    assert minutes[0]["time"] == "09:31"
    assert minutes[-1]["time"] == "15:00"
    assert str(minutes[0]["datetime"]).startswith(completed_day)


def test_live_etf_transaction_precision_and_derived_fields(live_client: SyncClient) -> None:
    daily = live_client.bars("510300", frequency=9, start=0, offset=10)
    completed_day, completed = _completed_bar(daily)
    history = live_client.transactions_day("510300", completed_day.replace("-", ""))
    assert history

    low = float(completed["low"])
    high = float(completed["high"])
    assert all(low - 0.001 <= float(row["price"]) <= high + 0.001 for row in history)
    assert all(str(row["datetime"]).startswith(completed_day) for row in history)
    assert all(row["side_name"] in {"buy", "sell", "neutral"} for row in history)
    for row in history[:100]:
        assert float(row["amount"]) == pytest.approx(
            float(row["price"]) * float(row["volume"]) * 100
        )

    current = live_client.transaction("510300", start=0, offset=1800)
    if str(daily[-1]["datetime"])[:10] == date.today().isoformat():
        assert current
    if current:
        latest = daily[-1]
        assert all(
            float(latest["low"]) - 0.001
            <= float(row["price"])
            <= float(latest["high"]) + 0.001
            for row in current
        )
        with_orders = next(row for row in current if int(row["num"]) > 0)
        assert float(with_orders["average_volume"]) == pytest.approx(
            float(with_orders["volume"]) / int(with_orders["num"])
        )
        assert float(with_orders["average_amount"]) == pytest.approx(
            float(with_orders["amount"]) / int(with_orders["num"])
        )

    chunks = list(
        live_client.iter_transactions(
            "510300",
            completed_day.replace("-", ""),
            completed_day.replace("-", ""),
        )
    )
    assert chunks == [(completed_day.replace("-", ""), history)]


def test_live_stock_bar_volume_is_lots_across_daily_and_minute(
    live_client: SyncClient,
) -> None:
    daily = live_client.bars("600036", frequency=9, start=0, offset=10)
    minutes = live_client.bars("600036", frequency=8, start=0, offset=800)
    _, day_bar, day_minutes = _complete_intraday_group(daily, minutes)

    assert sum(float(row["volume"]) for row in day_minutes) == pytest.approx(
        float(day_bar["volume"]),
        abs=5,
    )
    assert sum(float(row["amount"]) for row in day_minutes) == pytest.approx(
        float(day_bar["amount"]),
        rel=1e-5,
    )
    assert all(float(row["volume"]) >= 0 for row in day_minutes)


@pytest.mark.parametrize(("symbol", "market"), [("000001", 1), ("399001", 0)])
def test_live_index_intraday_slot_is_turnover_not_lot_volume(
    live_client: SyncClient,
    symbol: str,
    market: int,
) -> None:
    daily = live_client.index_bars(symbol, frequency=9, start=0, offset=10, market=market)
    minutes = live_client.index_bars(symbol, frequency=8, start=0, offset=800, market=market)
    _, day_bar, day_minutes = _complete_intraday_group(daily, minutes)

    assert day_bar["volume_unit"] == "lot"
    assert day_bar["volume_lots"] == day_bar["volume"]
    assert all(row["volume_unit"] == "hundred_yuan_turnover" for row in day_minutes)
    assert all(row["volume_lots"] is None for row in day_minutes)
    assert all(row["turnover_100_yuan"] == row["volume"] for row in day_minutes)
    assert sum(float(row["turnover_100_yuan"]) for row in day_minutes) == pytest.approx(
        sum(float(row["amount"]) for row in day_minutes) / 100,
        rel=1e-6,
    )

    if market == 1:
        facade = PandasClient(raw_client=live_client)
        native = facade.index_bars(symbol, frequency=9, offset=2, market=market)
        legacy = facade.index(symbol, frequency=9, offset=2, market=market)
        assert native.iloc[-1]["volume"] == native.iloc[-1]["volume_lots"]
        assert legacy.iloc[-1]["volume"] == native.iloc[-1]["volume_raw"]
        assert "volume_unit" not in legacy.columns


def test_live_xdxr_context_share_units_and_valuation(live_client: SyncClient) -> None:
    rows = live_client.xdxr("600036")
    assert rows
    assert all(row["market"] == 1 and row["code"] == "600036" for row in rows)
    assert all({"datetime", "raw_c1", "raw_c2", "raw_c3", "raw_c4"} <= set(row) for row in rows)

    equity_rows = [
        row
        for row in rows
        if row["panhouliutong_shares"] is not None and row["houzongguben_shares"] is not None
    ]
    assert equity_rows
    latest_equity = equity_rows[-1]
    assert float(latest_equity["panhouliutong_shares"]) == pytest.approx(
        float(latest_equity["panhouliutong_wan_shares"]) * 10_000
    )
    assert float(latest_equity["houzongguben_shares"]) == pytest.approx(
        float(latest_equity["houzongguben_wan_shares"]) * 10_000
    )

    grouped = live_client.xdxr_by_date("600036")
    assert sum(len(events) for events in grouped.values()) == len(rows)

    daily = live_client.bars("600036", frequency=9, start=0, offset=1)[0]
    day = str(daily["datetime"])[:10]
    price = float(daily["close"])
    value = live_client.market_value("600036", day, price)
    turnover = live_client.turnover(
        "600036",
        day,
        float(daily["volume"]),
        volume_unit="lots",
    )
    assert value is not None and turnover is not None
    assert value["float_market_value"] == pytest.approx(price * float(value["float_shares"]))
    assert turnover == pytest.approx(
        float(daily["volume"]) * 100 / float(value["float_shares"]) * 100
    )


def test_live_pagination_calendar_and_context_fields(live_client: SyncClient) -> None:
    latest_transactions = live_client.transaction("600036", start=0, offset=800)
    all_transactions = live_client.transaction_all("600036", page_size=800)
    assert len(all_transactions) > 800
    assert all_transactions[-len(latest_transactions) :] == latest_transactions

    bars = live_client.bars_all("600036", frequency=9, max_pages=2)
    index_bars = live_client.index_bars_all(
        "000001",
        frequency=9,
        market=1,
        max_pages=2,
    )
    assert len(bars) == 1600
    assert len(index_bars) == 1600
    assert bars == live_client.bars_until(
        "600036",
        lambda row: row["datetime"] <= bars[0]["datetime"],
        frequency=9,
        max_pages=2,
    )
    assert index_bars == live_client.index_bars_until(
        "000001",
        lambda row: row["datetime"] <= index_bars[0]["datetime"],
        frequency=9,
        market=1,
        max_pages=2,
    )
    assert all(
        bars[index]["previous_close"] == bars[index - 1]["close"]
        for index in range(1, len(bars))
    )

    recent_days = live_client.trading_days(
        str(bars[-10]["datetime"])[:10],
        str(bars[-1]["datetime"])[:10],
    )
    assert recent_days == tuple(str(row["datetime"])[:10].replace("-", "") for row in bars[-10:])

    quote = live_client.quotes("600036")[0]
    finance = live_client.finance("600036")
    assert quote["rate"] == pytest.approx(int(quote["rate_raw"]) / 100)
    assert isinstance(quote["trading_phase"], int)
    assert finance["touzishouyi"] == finance["touzishouyu"]


def test_live_async_precision_units_and_context_parity(live_client: SyncClient) -> None:
    etf_daily = live_client.bars("588000", frequency=9, start=0, offset=10)
    etf_day, etf_bar = _completed_bar(etf_daily)
    transaction_daily = live_client.bars("510300", frequency=9, start=0, offset=10)
    transaction_day, transaction_bar = _completed_bar(transaction_daily)

    async def run() -> tuple[object, object, object, object]:
        client = AsyncClient(servers=_servers(), max_retries=2)
        try:
            return await asyncio.gather(
                client.quotes("588000"),
                client.minutes("588000", etf_day.replace("-", "")),
                client.transactions("510300", transaction_day.replace("-", ""), 0, 2000),
                client.index_bars("000001", 8, 0, 10, 1),
            )
        finally:
            client.close()

    quotes, minutes, transactions, index_bars = asyncio.run(run())
    assert isinstance(quotes, list) and quotes
    assert isinstance(minutes, list) and minutes
    assert isinstance(transactions, list) and transactions
    assert isinstance(index_bars, list) and index_bars
    assert float(minutes[-1]["price"]) == pytest.approx(float(etf_bar["close"]), abs=0.001)
    assert all(
        float(transaction_bar["low"]) - 0.001
        <= float(row["price"])
        <= float(transaction_bar["high"]) + 0.001
        for row in transactions
    )
    assert all(row["volume_unit"] == "hundred_yuan_turnover" for row in index_bars)
