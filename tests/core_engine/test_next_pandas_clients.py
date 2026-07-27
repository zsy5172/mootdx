from __future__ import annotations

import asyncio
import inspect

import pandas as pd
import pandas.testing as pdt

from mootdx_next import AsyncPandasClient
from mootdx_next import PandasClient


def _bar(date: str, close: float = 10.0) -> dict[str, object]:
    timestamp = pd.Timestamp(date)
    return {
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "vol": 100,
        "amount": 1000.0,
        "year": timestamp.year,
        "month": timestamp.month,
        "day": timestamp.day,
        "hour": 0,
        "minute": 0,
        "datetime": timestamp.strftime("%Y-%m-%d 00:00:00"),
    }


class SyncRaw:
    def __init__(self) -> None:
        self.closed = False
        self.bar_calls: list[tuple[str, int, int, int]] = []

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False

    def stock_count(self, market: int):
        return 1

    def stocks(self, market: int):
        return [{"code": "600036", "name": "招商银行"}]

    def quotes(self, symbol=None):
        return [{"code": "600036", "price": 10.0, "vol": 100}]

    def bars(self, symbol: str, frequency=9, start=0, offset=800):
        self.bar_calls.append((symbol, frequency, start, offset))
        return [_bar("2024-01-02", 10.0), _bar("2024-01-03", 11.0)] if start == 0 else []

    def minutes(self, symbol: str, date):
        return [{"date": f"{date} 09:30:00", "price": 10.0, "vol": 100}]

    def transaction(self, symbol: str, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def transactions(self, symbol: str, date, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def finance(self, symbol: str):
        return {"code": symbol, "liutongguben": 1.0}

    def xdxr(self, symbol: str):
        return []

    def index_bars(self, symbol: str, frequency=9, start=0, offset=800, market=None):
        return [_bar("2024-01-03", 3000.0)]

    def block(self, block_file="block.dat"):
        return [{"blockname": "测试", "code": "600036"}]

    def f10_categories(self, symbol: str):
        return [{"name": "最新提示", "filename": "600036.txt", "start": 0, "length": 4}]

    def f10_content(self, symbol: str, name: str):
        return f"{name}内容"


class AsyncRaw:
    def __init__(self) -> None:
        self.sync = SyncRaw()

    @property
    def closed(self) -> bool:
        return self.sync.closed

    def close(self) -> None:
        self.sync.close()

    def reconnect(self) -> None:
        self.sync.reconnect()

    def __getattr__(self, name):
        method = getattr(self.sync, name)

        async def call(*args, **kwargs):
            return method(*args, **kwargs)

        return call


class EmptyTransactionRaw(SyncRaw):
    def transaction(self, symbol: str, start=0, offset=800):
        return []


class AsyncEmptyTransactionRaw(AsyncRaw):
    def __init__(self) -> None:
        self.sync = EmptyTransactionRaw()


def test_pandas_client_exposes_native_and_compatibility_methods() -> None:
    raw = SyncRaw()
    client = PandasClient(raw_client=raw)

    assert isinstance(client.quotes("600036"), pd.DataFrame)
    assert isinstance(client.bars("600036"), pd.DataFrame)
    assert isinstance(client.stocks(1), pd.DataFrame)
    assert isinstance(client.finance("600036"), pd.DataFrame)
    assert client.F10C("600036") == client.f10_categories("600036")
    assert client.F10("600036", "最新提示") == "最新提示内容"
    pdt.assert_frame_equal(client.index("000001", market=1), client.index_bars("000001", market=1))


def test_empty_transaction_is_preserved_by_sync_and_async_pandas_clients() -> None:
    async def run() -> None:
        sync_client = PandasClient(raw_client=EmptyTransactionRaw())
        async_client = AsyncPandasClient(raw_client=AsyncEmptyTransactionRaw())

        assert sync_client.transaction("600036").empty
        assert (await async_client.transaction("600036")).empty

    asyncio.run(run())


def test_history_entrypoints_keep_distinct_public_signatures() -> None:
    assert list(inspect.signature(PandasClient.get_k_data).parameters) == [
        "self",
        "code",
        "start_date",
        "end_date",
        "adjust",
    ]
    assert list(inspect.signature(PandasClient.k).parameters) == ["self", "symbol", "begin", "end", "kwargs"]
    assert list(inspect.signature(PandasClient.ohlc).parameters) == ["self", "kwargs"]


def test_history_entrypoints_share_results_without_becoming_aliases() -> None:
    client = PandasClient(raw_client=SyncRaw())

    get_k_data = client.get_k_data("600036", "2024-01-02", "2024-01-03")
    k_data = client.k("600036", "2024-01-02", "2024-01-03")
    ohlc = client.ohlc(symbol="600036", begin="2024-01-02", end="2024-01-03")

    pdt.assert_frame_equal(k_data, ohlc)
    pdt.assert_frame_equal(get_k_data, k_data.drop(columns=["volume"]))
    assert "volume" not in get_k_data.columns
    assert k_data["volume"].equals(k_data["vol"])
    assert "code" in get_k_data.columns
    assert "datetime" not in get_k_data.columns


def test_async_pandas_client_matches_sync_shapes_and_adjustment() -> None:
    async def run() -> None:
        sync_client = PandasClient(raw_client=SyncRaw())
        async_client = AsyncPandasClient(raw_client=AsyncRaw())

        sync_quotes = sync_client.quotes("600036")
        async_quotes = await async_client.quotes("600036")
        pdt.assert_frame_equal(sync_quotes, async_quotes)

        sync_adjusted = sync_client.bars("600036", adjust="qfq")
        async_adjusted = await async_client.bars("600036", adjust="qfq")
        pdt.assert_frame_equal(sync_adjusted, async_adjusted)

        async_k = await async_client.k("600036", "2024-01-02", "2024-01-03")
        async_ohlc = await async_client.ohlc(
            symbol="600036",
            begin="2024-01-02",
            end="2024-01-03",
        )
        pdt.assert_frame_equal(async_k, async_ohlc)

    asyncio.run(run())
