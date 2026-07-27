from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import mootdx.quotes as quotes_module
import mootdx_next.api.pandas as pandas_api_module
import mootdx_next.candidates as candidates_module
from mootdx.consts import HQ_HOSTS
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextStdQuotes
from mootdx.quotes import Quotes
from mootdx_next import CandidateRegistry
from mootdx_next import invalidate_hq_candidates
from mootdx_next import refresh_hq_candidates
from mootdx_next import ServerCandidate
from mootdx_next import ServerEndpoint


class DummyNextClient:
    def __init__(self) -> None:
        self.closed = False
        self.last_bars_call: dict[str, object] | None = None
        self.last_minutes_call: dict[str, object] | None = None
        self.last_index_bars_call: dict[str, object] | None = None

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False

    def quotes(self, symbol=None):
        if isinstance(symbol, list):
            return [{"code": item, "price": 1.0, "vol": 10} for item in symbol]
        return [{"code": symbol, "price": 1.0, "vol": 10}]

    def bars(self, symbol: str, frequency: int | str = 9, start: int = 0, offset: int = 800):
        self.last_bars_call = {
            "symbol": symbol,
            "frequency": frequency,
            "start": start,
            "offset": offset,
        }
        return [
            {
                "open": 10.0,
                "close": 11.0,
                "high": 12.0,
                "low": 9.5,
                "vol": 1000,
                "amount": 10000.0,
                "year": 2026,
                "month": 4,
                "day": 12,
                "hour": 0,
                "minute": 0,
                "datetime": "2026-04-12 00:00:00",
            }
        ]

    def index_bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
        market: int | None = None,
    ):
        self.last_index_bars_call = {
            "symbol": symbol,
            "frequency": frequency,
            "start": start,
            "offset": offset,
            "market": market,
        }
        return [
            {
                "open": 3000.0,
                "close": 3001.0,
                "high": 3002.0,
                "low": 2999.0,
                "vol": 1000,
                "amount": 10000.0,
                "year": 2026,
                "month": 4,
                "day": 12,
                "hour": 0,
                "minute": 0,
                "datetime": "2026-04-12 00:00:00",
                "up_count": 10,
                "down_count": 5,
            }
        ]

    def stock_count(self, market: int):
        return 2 if market in {0, 1} else 1

    def stocks(self, market: int):
        prefix = "sh" if market == 1 else "sz"
        return [{"code": f"{prefix}000001", "name": prefix.upper(), "vol": 1}]

    def minutes(self, symbol: str, date: str | int):
        self.last_minutes_call = {"symbol": symbol, "date": str(date)}
        return [{"price": 10.0, "vol": 100, "date": str(date)}]

    def transaction(self, symbol: str, start: int = 0, offset: int = 800):
        return [{"time": "09:31", "price": 10.0, "vol": 100, "num": 1, "buyorsell": 0}]

    def transactions(self, symbol: str, date: str | int, start: int = 0, offset: int = 800):
        return [{"time": "09:31", "price": 10.0, "vol": 100, "buyorsell": 0}]

    def f10_categories(self, symbol: str):
        return [
            {"name": "公司概况", "filename": "600036.txt", "start": 0, "length": 10},
            {"name": "最新提示", "filename": "600036.txt", "start": 10, "length": 20},
        ]

    def f10_content(self, symbol: str, name: str):
        return f"{name}内容"

    def xdxr(self, symbol: str):
        return [{"year": 2026, "month": 4, "day": 12, "category": 1, "name": "除权除息"}]

    def finance(self, symbol: str):
        return {"code": symbol, "liutongguben": 100.0}

    def block(self, block_file: str = "block.dat"):
        return [{"blockname": "测试板块", "block_type": 1, "code_index": 0, "code": "600036"}]


class EmptyTransactionNextClient(DummyNextClient):
    def transaction(self, symbol: str, start: int = 0, offset: int = 800):
        return []


def _client() -> NextStdQuotes:
    return NextStdQuotes(server=("127.0.0.1", 7709), engine_client=DummyNextClient())


def test_factory_returns_next_std_quotes() -> None:
    client = Quotes.factory(market="std", engine="next", server=("127.0.0.1", 7709), engine_client=DummyNextClient())
    assert isinstance(client, NextStdQuotes)


def test_factory_defaults_std_market_to_next_engine() -> None:
    client = Quotes.factory(market="std", server=("127.0.0.1", 7709), engine_client=DummyNextClient())
    assert isinstance(client, NextStdQuotes)


def test_next_factory_transaction_preserves_empty_upstream_result() -> None:
    client = Quotes.factory(
        market="std",
        engine="next",
        server=("127.0.0.1", 7709),
        engine_client=EmptyTransactionNextClient(),
    )

    try:
        result = client.transaction(symbol="600036")
    finally:
        client.close()

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_next_factory_uses_full_server_pool_without_legacy_config(monkeypatch) -> None:
    def fail_legacy_config(*args, **kwargs) -> None:
        pytest.fail("next engine must not initialize the legacy bestip configuration")

    monkeypatch.setattr(quotes_module.config, "setup", fail_legacy_config)

    client = Quotes.factory(market="std", engine="next")
    try:
        servers = client.client.scheduler.servers
        assert [(server.label, server.host, server.port) for server in servers] == HQ_HOSTS
    finally:
        client.close()


def test_next_factory_bestip_probes_without_writing_legacy_config(monkeypatch) -> None:
    calls = []
    original_config = quotes_module.config.clone()
    responses = [
        [
            ServerCandidate(HQ_HOSTS[1][1], HQ_HOSTS[1][2], HQ_HOSTS[1][0]),
            ServerCandidate(HQ_HOSTS[0][1], HQ_HOSTS[0][2], HQ_HOSTS[0][0]),
        ],
        [
            ServerCandidate(HQ_HOSTS[2][1], HQ_HOSTS[2][2], HQ_HOSTS[2][0]),
            ServerCandidate(HQ_HOSTS[0][1], HQ_HOSTS[0][2], HQ_HOSTS[0][0]),
        ],
    ]

    def probe():
        calls.append(len(calls) + 1)
        return responses[len(calls) - 1]

    registry = CandidateRegistry(probe)
    monkeypatch.setattr(candidates_module, "_hq_candidate_registry", registry)

    first = Quotes.factory(market="std", engine="next", bestip=True)
    second = Quotes.factory(market="std", engine="next", bestip=True)
    try:
        assert calls == [1]
        assert first.bestip == (HQ_HOSTS[1][1], HQ_HOSTS[1][2])
        assert second.bestip == first.bestip
        assert [(server.host, server.port) for server in first.client.scheduler.servers] == [
            (HQ_HOSTS[1][1], HQ_HOSTS[1][2]),
            (HQ_HOSTS[0][1], HQ_HOSTS[0][2]),
        ]
        assert first.client.connection_pool is not second.client.connection_pool
        assert first.client.scheduler.servers[0] is not second.client.scheduler.servers[0]

        refreshed = refresh_hq_candidates()
        third = Quotes.factory(market="std", engine="next", bestip=True)
        try:
            assert len(calls) == 2
            assert refreshed[0].host == HQ_HOSTS[2][1]
            assert third.bestip == (HQ_HOSTS[2][1], HQ_HOSTS[2][2])
            assert first.bestip == (HQ_HOSTS[1][1], HQ_HOSTS[1][2])
        finally:
            third.close()

        assert quotes_module.config.clone() == original_config
    finally:
        first.close()
        second.close()
        invalidate_hq_candidates()


def test_next_factory_respects_explicit_server(monkeypatch) -> None:
    def fail_probe(*args, **kwargs) -> None:
        pytest.fail("an explicit server must take precedence over bestip probing")

    monkeypatch.setattr(pandas_api_module, "get_hq_candidates", fail_probe)
    client = Quotes.factory(market="std", engine="next", server=("127.0.0.1", 7709), bestip=True)
    try:
        assert client.server == ("127.0.0.1", 7709)
        assert client.bestip == ("127.0.0.1", 7709)
        assert client.client.scheduler.servers == [
            ServerEndpoint(host="127.0.0.1", port=7709, label="std-next")
        ]
    finally:
        client.close()


def test_factory_rejects_ext_market() -> None:
    with pytest.raises(MootdxValidationException, match="扩展市场已经废弃且不再支持"):
        Quotes.factory(market="ext", engine="next")


def test_next_quotes_compat_returns_dataframe_and_empty_for_none() -> None:
    client = _client()
    assert isinstance(client.quotes(symbol="600036"), pd.DataFrame)
    assert client.quotes(symbol=None).empty is True


def test_next_stock_compat_returns_legacy_shapes() -> None:
    client = _client()

    assert client.stock_count(0) == 2
    assert isinstance(client.stocks(0), pd.DataFrame)
    assert isinstance(client.stock_all(), pd.DataFrame)


def test_next_bars_clamps_offset() -> None:
    client = _client()

    data = client.bars(symbol="600036", offset=999)

    assert isinstance(data, pd.DataFrame)
    assert client.client.last_bars_call["offset"] == 800


def test_next_minute_matches_minutes_today() -> None:
    client = _client()
    today = datetime.now().strftime("%Y%m%d")

    data0 = client.minute(symbol="000001")
    data1 = client.minutes(symbol="000001", date=today)

    assert data0.equals(data1)
    assert client.client.last_minutes_call == {"symbol": "000001", "date": today}


def test_next_transactions_and_info_return_legacy_shapes() -> None:
    client = _client()

    assert isinstance(client.transactions(symbol="600036", date="20170209"), pd.DataFrame)
    assert isinstance(client.finance(symbol="000001"), pd.DataFrame)
    assert isinstance(client.xdxr(symbol="600036"), pd.DataFrame)
    assert isinstance(client.F10C(symbol="600036"), list)
    assert client.F10(symbol="600036", name="公司概况") == "公司概况内容"


def test_next_index_and_block_return_legacy_shapes() -> None:
    client = _client()

    index_data = client.index(symbol="000001", frequency=9, start=1, offset=2, market=1)
    block_data = client.block()

    assert isinstance(index_data, pd.DataFrame)
    assert isinstance(block_data, pd.DataFrame)
    assert client.client.last_index_bars_call == {
        "symbol": "000001",
        "frequency": 9,
        "start": 1,
        "offset": 2,
        "market": 1,
    }


def test_next_k_and_ohlc_use_get_k_data_shape() -> None:
    client = _client()

    data = client.k(symbol="600036", begin="2026-04-10", end="2026-04-12")
    ohlc = client.ohlc(symbol="600036", begin="2026-04-10", end="2026-04-12")
    raw = client.get_k_data(code="600036", start_date="2026-04-10", end_date="2026-04-12")

    assert isinstance(data, pd.DataFrame)
    assert isinstance(ohlc, pd.DataFrame)
    assert isinstance(raw, pd.DataFrame)
    assert "code" in raw.columns
    assert "datetime" not in raw.columns


def test_next_get_k_data_pages_back_until_requested_history(monkeypatch) -> None:
    class PagedBarsClient(DummyNextClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        def bars(self, symbol: str, frequency: int | str = 9, start: int = 0, offset: int = 800):
            self.calls.append((start, offset))
            pages = {
                0: ["2026-07-20 00:00:00", "2026-07-17 00:00:00"],
                2: ["2019-07-10 00:00:00", "2019-07-03 00:00:00"],
            }
            return [
                {
                    "open": 10.0,
                    "close": 11.0,
                    "high": 12.0,
                    "low": 9.0,
                    "vol": 100,
                    "amount": 1000.0,
                    "datetime": value,
                }
                for value in pages.get(start, [])
            ]

    monkeypatch.setattr(pandas_api_module, "KLINE_PAGE_SIZE", 2)
    engine_client = PagedBarsClient()
    client = NextStdQuotes(server=("127.0.0.1", 7709), engine_client=engine_client)

    result = client.get_k_data(code="000001", start_date="2019-07-03", end_date="2019-07-10")

    assert engine_client.calls == [(0, 2), (2, 2)]
    assert list(result.index.strftime("%Y-%m-%d")) == ["2019-07-03", "2019-07-10"]
    assert set(result["code"]) == {"000001"}


def test_next_f10_unknown_name_falls_back_to_full_dict() -> None:
    client = _client()

    result = client.F10(symbol="600036", name="不存在的栏目")

    assert isinstance(result, dict)
    assert result == {"公司概况": "公司概况内容", "最新提示": "最新提示内容"}


def test_next_close_reconnect_and_closed() -> None:
    client = _client()

    assert client.closed is False
    client.close()
    assert client.closed is True
    client.reconnect()
    assert client.closed is False


def test_next_engine_rejects_ext_and_invalid_engine() -> None:
    with pytest.raises(MootdxValidationException):
        Quotes.factory(market="ext", engine="next")

    with pytest.raises(MootdxValidationException):
        Quotes.factory(market="std", engine="unknown")
