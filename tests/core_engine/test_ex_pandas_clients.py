from __future__ import annotations

import asyncio
import inspect

import pandas as pd
import pytest

import mootdx_next.api.ex_pandas as ex_pandas_module
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextExtQuotes
from mootdx.quotes import Quotes
from mootdx_next.api.ex_pandas import AsyncExPandasClient
from mootdx_next.api.ex_pandas import ExPandasClient
from mootdx_next.candidates import ServerCandidate
from mootdx_next.errors import InvalidSymbolError


EX_PANDAS_PUBLIC_API = {
    "raw_client",
    "closed",
    "close",
    "reconnect",
    "metrics",
    "traffic",
    "validate",
    "markets",
    "instrument",
    "instrument_count",
    "instruments",
    "quote",
    "quotes",
    "bars",
    "minute",
    "minutes",
    "transaction",
    "transactions",
    "bars_range",
}


def _public_api(owner: type) -> set[str]:
    return {
        name
        for name, value in owner.__dict__.items()
        if not name.startswith("_") and (inspect.isroutine(value) or isinstance(value, property))
    }


def test_sync_and_async_ex_pandas_clients_have_exact_api_parity() -> None:
    assert _public_api(ExPandasClient) == EX_PANDAS_PUBLIC_API
    assert _public_api(AsyncExPandasClient) == EX_PANDAS_PUBLIC_API


class ExFacadeRecorder:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False

    def markets(self):
        return [{"market": 31, "category": 2, "name": "香港主板", "short_name": "KH"}]

    def instrument(self, start=0, offset=100):
        self.calls.append(("instrument", (start, offset)))
        return [
            {
                "start": start,
                "category": 2,
                "market": 31,
                "code": "00700",
                "name": "腾讯控股",
                "description": "",
            }
        ]

    def instrument_count(self):
        return 1

    def instruments(self, page_size=1000):
        self.calls.append(("instruments", (page_size,)))
        return self.instrument(0, 1)

    def quote(self, market, symbol):
        self.calls.append(("quote", (market, symbol)))
        return {"market": market, "code": symbol, "price": 466.4}

    def quotes(self, market, category, start=0, offset=100):
        self.calls.append(("quotes", (market, category, start, offset)))
        return [{"market": market, "code": "00700", "price": 466.4}]

    def bars(self, market, symbol, frequency=9, start=0, offset=700):
        self.calls.append(("bars", (market, symbol, frequency, start, offset)))
        return [
            {
                "datetime": "2026-07-29 15:00",
                "open": 460.0,
                "high": 470.0,
                "low": 455.0,
                "close": 466.4,
                "trade": 100,
            }
        ]

    def minute(self, market, symbol):
        self.calls.append(("minute", (market, symbol)))
        return [{"time": "09:30", "price": 466.4, "average_price": 465.8}]

    def minutes(self, market, symbol, date):
        self.calls.append(("minutes", (market, symbol, date)))
        return self.minute(market, symbol)

    def transaction(self, market, symbol, start=0, offset=1800):
        self.calls.append(("transaction", (market, symbol, start, offset)))
        return [{"time": "09:30:01", "price": 466.4, "price_raw": 466400}]

    def transactions(self, market, symbol, date, start=0, offset=1800):
        self.calls.append(("transactions", (market, symbol, date, start, offset)))
        return [
            {
                "time": "09:30:01",
                "datetime": "2026-07-29 09:30:01",
                "price": 466.4,
                "price_raw": 466400,
            }
        ]

    def bars_range(self, market, symbol, start, end):
        self.calls.append(("bars_range", (market, symbol, start, end)))
        return self.bars(market, symbol)


class AsyncExFacadeRecorder(ExFacadeRecorder):
    async def markets(self):
        return super().markets()

    async def instrument(self, start=0, offset=100):
        return super().instrument(start, offset)

    async def instrument_count(self):
        return super().instrument_count()

    async def instruments(self, page_size=1000):
        return super().instruments(page_size)

    async def quote(self, market, symbol):
        return super().quote(market, symbol)

    async def quotes(self, market, category, start=0, offset=100):
        return super().quotes(market, category, start, offset)

    async def bars(self, market, symbol, frequency=9, start=0, offset=700):
        return super().bars(market, symbol, frequency, start, offset)

    async def minute(self, market, symbol):
        return super().minute(market, symbol)

    async def minutes(self, market, symbol, date):
        self.calls.append(("minutes", (market, symbol, date)))
        return [{"time": "09:30", "price": 466.4, "average_price": 465.8}]

    async def transaction(self, market, symbol, start=0, offset=1800):
        return super().transaction(market, symbol, start, offset)

    async def transactions(self, market, symbol, date, start=0, offset=1800):
        return super().transactions(market, symbol, date, start, offset)

    async def bars_range(self, market, symbol, start, end):
        self.calls.append(("bars_range", (market, symbol, start, end)))
        return [
            {
                "datetime": "2026-07-29 09:31",
                "open": 460.0,
                "high": 470.0,
                "low": 455.0,
                "close": 466.4,
            }
        ]


def test_ex_pandas_facade_shapes_and_market_prefix_compatibility() -> None:
    recorder = ExFacadeRecorder()
    client = ExPandasClient(engine_client=recorder)

    markets = client.markets()
    quote = client.quote(symbol="31#00700")
    bars = client.bars(symbol="31#00700", frequency="day", offset=1)
    minute = client.minute(31, "00700")
    current_trades = client.transaction(symbol="31#00700", offset=1)
    history_trades = client.transactions(symbol="31#00700", date=20260729, offset=1)

    assert list(markets.columns) == ["market", "category", "name", "short_name"]
    assert quote.iloc[0]["price"] == pytest.approx(466.4)
    assert isinstance(bars.index, pd.DatetimeIndex)
    assert bars.index[0] == pd.Timestamp("2026-07-29 15:00")
    assert minute.index.tolist() == ["09:30"]
    assert current_trades.index.tolist() == ["09:30:01"]
    assert history_trades.index[0] == pd.Timestamp("2026-07-29 09:30:01")
    assert ("quote", (31, "00700")) in recorder.calls


def test_ex_pandas_empty_frames_are_stable() -> None:
    recorder = ExFacadeRecorder()
    recorder.markets = lambda: []
    recorder.instrument = lambda start=0, offset=100: []
    recorder.quote = lambda market, symbol: None
    client = ExPandasClient(engine_client=recorder)

    assert list(client.markets().columns) == ["market", "category", "name", "short_name"]
    assert list(client.instrument().columns) == [
        "start",
        "category",
        "market",
        "code",
        "name",
        "description",
    ]
    assert client.quote(symbol="31#00700").empty


@pytest.mark.parametrize(
    ("market", "symbol"),
    [
        (47, "31#00700"),
        (None, "00700"),
        (31, ""),
        (31, "代码"),
        (31, "TOO-LONG-10"),
    ],
)
def test_ex_symbol_validation_rejects_ambiguous_inputs(market: object, symbol: object) -> None:
    with pytest.raises(InvalidSymbolError if market not in {None, ""} else Exception):
        ExPandasClient.validate(market, symbol)


def test_next_ext_factory_maps_validation_errors_and_preserves_lifecycle() -> None:
    recorder = ExFacadeRecorder()
    client = Quotes.factory(market="ext", engine="next", engine_client=recorder)
    assert isinstance(client, NextExtQuotes)
    assert client.traffic() is None

    with pytest.raises(MootdxValidationException, match="市场参数不能为空"):
        client.quote(symbol="00700")

    client.close()
    assert client.closed
    client.reconnect()
    assert not client.closed


def test_async_ex_pandas_facade_matches_sync_shapes() -> None:
    client = AsyncExPandasClient(engine_client=AsyncExFacadeRecorder())

    async def run():
        return (
            await client.markets(),
            await client.quote(symbol="31#00700"),
            await client.bars(symbol="31#00700", offset=1),
            await client.minute(symbol="31#00700"),
            await client.transactions(symbol="31#00700", date=20260729, offset=1),
            await client.bars_range(
                symbol="31#00700",
                start=20260101,
                end=20260729,
            ),
        )

    markets, quote, bars, minute, transactions, bars_range = asyncio.run(run())
    assert markets.shape == (1, 4)
    assert quote.iloc[0]["code"] == "00700"
    assert isinstance(bars.index, pd.DatetimeIndex)
    assert minute.index.tolist() == ["09:30"]
    assert isinstance(transactions.index, pd.DatetimeIndex)
    assert isinstance(bars_range.index, pd.DatetimeIndex)


def test_ex_bestip_uses_candidate_snapshot_and_explicit_server_wins(monkeypatch) -> None:
    calls = []

    def candidates():
        calls.append(1)
        return (
            ServerCandidate("116.205.143.214", 7727, "广州1", 20.0),
            ServerCandidate("127.0.0.2", 7727, "备用", 30.0),
        )

    monkeypatch.setattr(ex_pandas_module, "get_ex_candidates", candidates)
    client = ExPandasClient(bestip=True)
    explicit = ExPandasClient(server=("127.0.0.1", 7727), bestip=True)
    try:
        assert calls == [1]
        assert client.bestip == ("116.205.143.214", 7727)
        assert [(item.host, item.port) for item in client.client.scheduler.servers] == [
            ("116.205.143.214", 7727),
            ("127.0.0.2", 7727),
        ]
        assert explicit.bestip == ("127.0.0.1", 7727)
    finally:
        client.close()
        explicit.close()
