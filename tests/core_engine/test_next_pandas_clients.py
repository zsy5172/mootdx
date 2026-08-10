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
        "previous_close": close - 0.5,
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

    def quotes_all(self, symbol=None):
        return self.quotes(symbol=symbol)

    def limit_prices(self, start=0, count=2000):
        return [{"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14}]

    def price_limit(self, symbol: str, refresh=False):
        return {
            "market": 1,
            "code": "600036",
            "limit_up": 42.9,
            "limit_down": 35.1,
            "source": "calculated",
        }

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

    def block_catalog(self, category=None, refresh=False):
        return [
            {
                "name": "测试概念",
                "code": "880001",
                "category": "concept",
                "category_name": "概念板块",
            }
        ]

    def block_members(self, block, category=None, refresh=False):
        return [{"block_name": "测试概念", "block_code": "880001", "code": "600036"}]

    def block_members_all(self, category=None, refresh=False):
        return self.block_members("880001", category=category, refresh=refresh)

    def tdx_block_base(self, refresh=False):
        return [{"market": 1, "code": "880001", "circulating_market_cap": 1_000_000.0}]

    def fund_flows(self, symbol=None):
        return [{"code": "880001", "main_net_amount": 100.0, "main_net_amount_5min": 10.0}]

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


class NormalizedIndexRaw(SyncRaw):
    def index_bars(self, symbol: str, frequency=9, start=0, offset=800, market=None):
        row = _bar("2024-01-03", 3000.0)
        row.update(
            {
                "vol": 12_300.0,
                "volume": 12_300.0,
                "volume_raw": 123.0,
                "volume_unit": "lot",
                "volume_lots": 12_300.0,
                "turnover_100_yuan": None,
            }
        )
        return [row]


def test_pandas_client_exposes_native_and_compatibility_methods() -> None:
    raw = SyncRaw()
    client = PandasClient(raw_client=raw)

    assert isinstance(client.quotes("600036"), pd.DataFrame)
    assert isinstance(client.quotes_all(["600036"]), pd.DataFrame)
    assert list(client.limit_prices().columns) == ["market", "code", "limit_up", "limit_down"]
    assert list(client.price_limit("600036").columns) == [
        "market",
        "code",
        "limit_up",
        "limit_down",
        "source",
    ]
    assert isinstance(client.bars("600036"), pd.DataFrame)
    assert isinstance(client.stocks(1), pd.DataFrame)
    assert isinstance(client.finance("600036"), pd.DataFrame)
    assert client.block_catalog("概念").iloc[0]["code"] == "880001"
    assert client.block_members("880001").iloc[0]["code"] == "600036"
    assert client.block_members_all("概念").iloc[0]["code"] == "600036"
    assert client.tdx_block_base().iloc[0]["circulating_market_cap"] == 1_000_000.0
    assert client.fund_flows("880001").iloc[0]["main_net_amount"] == 100.0
    assert client.F10C("600036") == client.f10_categories("600036")
    assert client.F10("600036", "最新提示") == "最新提示内容"
    native_index = client.index_bars("000001", market=1)
    legacy_index = client.index("000001", market=1)
    pdt.assert_frame_equal(
        legacy_index,
        native_index.drop(columns=["previous_close"], errors="ignore"),
    )


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


def test_legacy_index_restores_raw_volume_while_native_keeps_units() -> None:
    client = PandasClient(raw_client=NormalizedIndexRaw())

    native = client.index_bars("000001", market=1)
    legacy = client.index("000001", market=1)

    assert native.iloc[0]["volume"] == 12_300
    assert native.iloc[0]["volume_unit"] == "lot"
    assert legacy.iloc[0]["volume"] == 123
    assert legacy.iloc[0]["vol"] == 123
    assert "volume_unit" not in legacy.columns


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
    assert "previous_close" not in get_k_data.columns
    assert "previous_close" not in k_data.columns


def test_async_pandas_client_matches_sync_shapes_and_adjustment() -> None:
    async def run() -> None:
        sync_client = PandasClient(raw_client=SyncRaw())
        async_client = AsyncPandasClient(raw_client=AsyncRaw())

        sync_quotes = sync_client.quotes("600036")
        async_quotes = await async_client.quotes("600036")
        pdt.assert_frame_equal(sync_quotes, async_quotes)
        pdt.assert_frame_equal(
            sync_client.quotes_all(["600036"]),
            await async_client.quotes_all(["600036"]),
        )

        pdt.assert_frame_equal(sync_client.limit_prices(), await async_client.limit_prices())
        pdt.assert_frame_equal(sync_client.tdx_block_base(), await async_client.tdx_block_base())
        pdt.assert_frame_equal(
            sync_client.block_members_all("概念"),
            await async_client.block_members_all("概念"),
        )
        pdt.assert_frame_equal(
            sync_client.fund_flows("880001"),
            await async_client.fund_flows("880001"),
        )
        pdt.assert_frame_equal(
            sync_client.price_limit("600036"),
            await async_client.price_limit("600036"),
        )

        for adjust in ("qfq", "tdx_qfq", "tdx_hfq"):
            sync_adjusted = sync_client.bars("600036", adjust=adjust)
            async_adjusted = await async_client.bars("600036", adjust=adjust)
            pdt.assert_frame_equal(sync_adjusted, async_adjusted)

        async_k = await async_client.k("600036", "2024-01-02", "2024-01-03")
        async_ohlc = await async_client.ohlc(
            symbol="600036",
            begin="2024-01-02",
            end="2024-01-03",
        )
        pdt.assert_frame_equal(async_k, async_ohlc)

    asyncio.run(run())
