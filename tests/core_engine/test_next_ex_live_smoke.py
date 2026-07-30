from __future__ import annotations

import asyncio
import os

import pandas as pd
import pytest

from mootdx.quotes import NextExtQuotes
from mootdx.quotes import Quotes
from mootdx_next.api.ex_clients import AsyncExClient
from mootdx_next.api.ex_clients import ExSyncClient
from mootdx_next.candidates import invalidate_ex_candidates
from mootdx_next.candidates import refresh_ex_candidates
from mootdx_next.constants import EX_HOSTS
from mootdx_next.models import ServerEndpoint

pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_NEXT_EX_LIVE") != "1",
    reason="set MOOTDX_NEXT_EX_LIVE=1 for the local ExHq live matrix",
)

EX_SERVER = os.getenv("MOOTDX_NEXT_EX_HOST", "116.205.143.214")
EX_PORT = int(os.getenv("MOOTDX_NEXT_EX_PORT", "7727"))
EX_HISTORY_DATE = os.getenv("MOOTDX_NEXT_EX_DATE", "20260729")
EX_MARKET = 31
EX_SYMBOL = "00700"


def _server() -> ServerEndpoint:
    return ServerEndpoint(EX_SERVER, EX_PORT, "ExHq live")


def test_ex_application_level_candidate_probe() -> None:
    try:
        candidates = refresh_ex_candidates()
        assert candidates
        assert all((item.label, item.host, item.port) in EX_HOSTS for item in candidates)
        assert all(item.latency_ms is not None and item.latency_ms > 0 for item in candidates)
    finally:
        invalidate_ex_candidates()


def test_ex_sync_complete_live_matrix() -> None:
    client = ExSyncClient(servers=[_server()], max_retries=0)
    try:
        markets = client.markets()
        count = client.instrument_count()
        first_page = client.instrument(0, 100)
        all_instruments = client.instruments(page_size=1000)
        quote = client.quote(EX_MARKET, EX_SYMBOL)
        quote_list = client.quotes(EX_MARKET, 2, 0, 100)
        bars = client.bars(EX_MARKET, EX_SYMBOL, 9, 0, 700)
        minute = client.minute(EX_MARKET, EX_SYMBOL)
        history_minute = client.minutes(EX_MARKET, EX_SYMBOL, EX_HISTORY_DATE)
        current_trades = client.transaction(EX_MARKET, EX_SYMBOL, 0, 1800)
        history_trades = client.transactions(
            EX_MARKET,
            EX_SYMBOL,
            EX_HISTORY_DATE,
            0,
            1800,
        )
        range_bars = client.bars_range(
            EX_MARKET,
            EX_SYMBOL,
            20260701,
            int(EX_HISTORY_DATE),
        )
    finally:
        client.close()

    assert any(row["market"] == EX_MARKET for row in markets)
    assert count > 100_000
    assert len(first_page) == 100
    # instrument_count is a server-side slot count and currently includes a
    # small reserved tail that decodes as an empty page.
    assert 0 < len(all_instruments) <= count
    assert count - len(all_instruments) < 1000
    assert all_instruments[-1]["start"] == len(all_instruments) - 1
    assert quote is not None and quote["pre_close"] > 0
    assert len(quote_list) == 100
    assert bars and 0 < len(bars) <= 700 and bars[-1]["close"] > 0
    # Current-session endpoints are allowed to be empty outside their market's
    # session. Their historical counterparts must provide stable evidence.
    assert isinstance(minute, list)
    assert history_minute and history_minute[0]["price"] > 0
    assert isinstance(current_trades, list)
    assert history_trades and history_trades[0]["price"] > 0
    assert history_trades[0]["price_raw"] == pytest.approx(
        history_trades[0]["price"] * 1000
    )
    assert range_bars and range_bars[0]["close"] > 0


def test_ex_async_workers_live_matrix() -> None:
    client = AsyncExClient(servers=[_server()], max_retries=0)

    async def run():
        return await asyncio.gather(
            client.markets(),
            client.quote(EX_MARKET, EX_SYMBOL),
            client.bars(EX_MARKET, EX_SYMBOL, 9, 0, 5),
            client.minutes(EX_MARKET, EX_SYMBOL, EX_HISTORY_DATE),
            client.transactions(EX_MARKET, EX_SYMBOL, EX_HISTORY_DATE, 0, 5),
        )

    try:
        markets, quote, bars, minutes, transactions = asyncio.run(run())
    finally:
        client.close()

    assert markets and quote and bars and minutes and transactions


def test_quotes_factory_ext_live_artifact() -> None:
    client = Quotes.factory(
        market="ext",
        engine="next",
        server=(EX_SERVER, EX_PORT),
    )
    try:
        quote = client.quote(symbol=f"{EX_MARKET}#{EX_SYMBOL}")
        bars = client.bars(symbol=f"{EX_MARKET}#{EX_SYMBOL}", frequency="day", offset=5)
        transactions = client.transactions(
            symbol=f"{EX_MARKET}#{EX_SYMBOL}",
            date=EX_HISTORY_DATE,
            offset=5,
        )
    finally:
        client.close()

    assert isinstance(client, NextExtQuotes)
    assert isinstance(quote, pd.DataFrame) and not quote.empty
    assert isinstance(bars.index, pd.DatetimeIndex) and not bars.empty
    assert isinstance(transactions.index, pd.DatetimeIndex) and not transactions.empty
