from __future__ import annotations

import asyncio
import os
from datetime import date
from datetime import time

import pandas as pd
import pytest

from mootdx_next import AsyncClient
from mootdx_next import SyncClient
from mootdx_next.analytics import aggregate_bars
from mootdx_next.analytics import atr
from mootdx_next.analytics import boll
from mootdx_next.analytics import ema
from mootdx_next.analytics import forward_returns
from mootdx_next.analytics import ma
from mootdx_next.analytics import macd
from mootdx_next.analytics import rsi
from mootdx_next.analytics import summarize_trade_sides
from mootdx_next.analytics import trades_to_minute_bars
from mootdx_next.analytics import vwap


pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_ANALYTICS_LIVE") != "1",
    reason="set MOOTDX_RUN_ANALYTICS_LIVE=1 to run live TDX analytics checks",
)


def _completed_day(rows: list[dict[str, object]]) -> str:
    today = date.today().strftime("%Y-%m-%d")
    historical = [str(row["datetime"])[:10] for row in rows if str(row["datetime"])[:10] < today]
    if historical:
        return historical[-1]
    if len(rows) < 2:
        raise AssertionError("live daily bars do not contain a completed trading day")
    return str(rows[-2]["datetime"])[:10]


def _regular_session(row: dict[str, object]) -> bool:
    value = time.fromisoformat(str(row["time"]))
    return time(9, 15) <= value <= time(11, 30) or time(13, 0) <= value <= time(15, 0)


@pytest.fixture(scope="module")
def live_data() -> dict[str, object]:
    client = SyncClient()
    try:
        daily_rows = client.bars("600036", frequency=9, start=0, offset=160)
        assert len(daily_rows) >= 30
        completed_day = _completed_day(daily_rows)
        transaction_rows = client.transactions_day(
            "600036",
            completed_day.replace("-", ""),
        )
        minute_rows = client.bars("600036", frequency=8, start=0, offset=480)
        assert transaction_rows
        assert minute_rows
        yield {
            "client": client,
            "daily_rows": daily_rows,
            "daily": pd.DataFrame.from_records(daily_rows),
            "completed_day": completed_day,
            "transactions": transaction_rows,
            "minute_rows": minute_rows,
        }
    finally:
        client.close()


def test_live_indicators_and_forward_returns(live_data: dict[str, object]) -> None:
    daily = live_data["daily"]
    assert isinstance(daily, pd.DataFrame)

    moving_average = ma(daily, 5)
    exponential_average = ema(daily, 12)
    macd_frame = macd(daily)
    rsi_series = rsi(daily, 14)
    boll_frame = boll(daily, 20)
    atr_series = atr(daily, 14)
    vwap_series = vwap(daily)
    returns = forward_returns(daily, horizons=(1, 5, 20))

    assert moving_average.iloc[-1] == pytest.approx(daily["close"].tail(5).mean())
    assert exponential_average.notna().all()
    assert list(macd_frame.columns) == ["dif", "dea", "histogram"]
    assert rsi_series.dropna().between(0, 100).all()
    assert (boll_frame["upper"].dropna() >= boll_frame["middle"].dropna()).all()
    assert (boll_frame["middle"].dropna() >= boll_frame["lower"].dropna()).all()
    assert (atr_series.dropna() >= 0).all()
    assert vwap_series.iloc[-1] == pytest.approx(
        daily["amount"].sum() / (daily["volume"].sum() * 100)
    )
    assert returns.iloc[0]["return_5"] == pytest.approx(
        daily.iloc[5]["close"] / daily.iloc[0]["close"] - 1
    )


def test_live_transactions_preserve_side_and_bar_totals(live_data: dict[str, object]) -> None:
    transactions = live_data["transactions"]
    completed_day = live_data["completed_day"]
    assert isinstance(transactions, list)
    assert isinstance(completed_day, str)

    summary = summarize_trade_sides(transactions)
    assert summary["total_trade_count"] == len(transactions)
    assert summary["total_volume"] == pytest.approx(
        sum(float(row["volume"]) for row in transactions)
    )
    assert summary["total_amount"] == pytest.approx(
        sum(float(row["amount"]) for row in transactions)
    )

    session_rows = [row for row in transactions if _regular_session(row)]
    minute_bars = trades_to_minute_bars(session_rows, date=completed_day)
    assert not minute_bars.empty
    assert minute_bars.iloc[0]["datetime"].endswith("09:30")
    assert bool(minute_bars.iloc[0]["is_call_auction"])
    assert minute_bars["volume"].sum() == pytest.approx(
        sum(float(row["volume"]) for row in session_rows)
    )
    assert minute_bars["amount"].sum() == pytest.approx(
        sum(float(row["amount"]) for row in session_rows)
    )


def test_live_minute_bar_aggregation_preserves_units_and_sessions(
    live_data: dict[str, object],
) -> None:
    minute_rows = live_data["minute_rows"]
    assert isinstance(minute_rows, list)

    five_minute = aggregate_bars(minute_rows, "5min")
    assert not five_minute.empty
    assert five_minute["volume"].sum() == pytest.approx(
        sum(float(row["volume"]) for row in minute_rows)
    )
    assert five_minute["amount"].sum() == pytest.approx(
        sum(float(row["amount"]) for row in minute_rows)
    )
    labels = pd.to_datetime(five_minute["datetime"])
    assert not labels.dt.time.map(lambda value: time(11, 31) <= value <= time(13, 0)).any()


def test_live_xdxr_market_value_and_async_parity(live_data: dict[str, object]) -> None:
    client = live_data["client"]
    daily_rows = live_data["daily_rows"]
    assert isinstance(client, SyncClient)
    assert isinstance(daily_rows, list)

    grouped = client.xdxr_by_date("600036")
    flattened = [row for rows in grouped.values() for row in rows]
    assert flattened
    assert all(str(row["datetime"])[:10] == event_date for event_date, rows in grouped.items() for row in rows)

    last_bar = daily_rows[-1]
    as_of = str(last_bar["datetime"])[:10]
    price = float(last_bar["close"])
    market_value = client.market_value("600036", as_of, price)
    assert market_value is not None
    assert market_value["float_market_value"] == pytest.approx(
        price * float(market_value["float_shares"])
    )
    assert market_value["total_market_value"] == pytest.approx(
        price * float(market_value["total_shares"])
    )

    async def run() -> tuple[object, object, object]:
        async_client = AsyncClient()
        try:
            return await asyncio.gather(
                async_client.bars("600036", frequency=9, start=0, offset=160),
                async_client.xdxr_by_date("600036"),
                async_client.market_value("600036", as_of, price),
            )
        finally:
            async_client.close()

    async_bars, async_grouped, async_market_value = asyncio.run(run())
    assert async_bars == daily_rows
    assert async_grouped == grouped
    assert async_market_value == market_value
