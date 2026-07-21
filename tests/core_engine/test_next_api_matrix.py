from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

import pandas as pd
import pytest

import mootdx_next.api.clients as clients_module
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextStdQuotes
from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import OutsideTradingSessionError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.params import FREQUENCY_ALIASES
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler


SYNC_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "request",
    "stock_count",
    "stocks",
    "quotes",
    "bars",
    "index_bars",
    "minutes",
    "minute",
    "transaction",
    "transactions",
    "finance",
    "block",
    "xdxr",
    "f10_categories",
    "f10_content",
}

ASYNC_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "request",
    "stock_count",
    "stocks",
    "quotes",
    "bars",
    "minutes",
    "minute",
    "transaction",
    "transactions",
    "finance",
    "xdxr",
    "index_bars",
    "block",
    "f10_categories",
    "f10_content",
}

NEXT_FACADE_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "traffic",
    "quotes",
    "bars",
    "stock_count",
    "stocks",
    "stock_all",
    "minute",
    "minutes",
    "transaction",
    "transactions",
    "F10C",
    "F10",
    "xdxr",
    "finance",
    "index_bars",
    "index",
    "block",
    "get_k_data",
    "k",
    "ohlc",
}

def _public_api(owner: type) -> set[str]:
    return {
        name
        for name, value in owner.__dict__.items()
        if not name.startswith("_") and (inspect.isroutine(value) or isinstance(value, property))
    }


def test_public_api_inventory_requires_matrix_updates_for_new_methods() -> None:
    assert _public_api(SyncClient) == SYNC_PUBLIC_API
    assert _public_api(AsyncClient) == ASYNC_PUBLIC_API
    assert _public_api(NextStdQuotes) == NEXT_FACADE_PUBLIC_API


def test_async_business_api_has_full_sync_parity() -> None:
    sync_business_api = SYNC_PUBLIC_API - {"closed", "close", "reconnect", "request"}
    async_business_api = ASYNC_PUBLIC_API - {"closed", "close", "reconnect", "request"}

    assert async_business_api == sync_business_api


class MatrixProtocol:
    def __init__(self) -> None:
        self.encode_calls: list[tuple[str, dict[str, object]]] = []
        self.decode_calls: list[tuple[str, dict[str, object]]] = []

    def encode(self, api: str, **kwargs: object) -> bytes:
        self.encode_calls.append((api, kwargs))
        return api.encode("ascii")

    def decode(self, api: str, envelope: ResponseEnvelope, **kwargs: object) -> object:
        self.decode_calls.append((api, kwargs))
        if api == "stock_count":
            return 0
        if api == "finance":
            return {"code": "600036"}
        if api == "block_info_meta":
            return {"size": 0, "hash": ""}
        if api == "f10_categories":
            return [{"name": "最新提示", "filename": "600036.txt", "start": 0, "length": 4}]
        if api == "f10_content":
            return "content"
        return []


class MatrixTransport:
    def __init__(self) -> None:
        self.metrics = TransportMetrics(last_latency_ms=1.0)
        self.contexts: list[RequestContext] = []
        self.payloads: list[bytes] = []
        self.closed = False

    def send(self, context: RequestContext, payload: bytes, server: ServerEndpoint) -> ResponseEnvelope:
        self.contexts.append(context)
        self.payloads.append(payload)
        return ResponseEnvelope(body=b"", server=server, elapsed_ms=1.0)

    def close(self) -> None:
        self.closed = True


def _matrix_client() -> tuple[SyncClient, MatrixProtocol, MatrixTransport]:
    protocol = MatrixProtocol()
    transport = MatrixTransport()
    pool = RecordingConnectionPool(transport)
    scheduler = RecordingScheduler(server=pool.server)
    client = SyncClient(protocol=protocol, connection_pool=pool, scheduler=scheduler)
    return client, protocol, transport


@dataclass(frozen=True)
class SyncCallCase:
    method: str
    kwargs: dict[str, object]
    protocol_apis: tuple[str, ...]


SYNC_CALL_CASES = (
    SyncCallCase("stock_count", {"market": 0}, ("stock_count",)),
    SyncCallCase("stocks", {"market": 1}, ("stock_count",)),
    SyncCallCase("quotes", {"symbol": ["600036", "sz000001"]}, ("quotes",)),
    SyncCallCase("bars", {"symbol": "sh600036", "frequency": "day", "start": 20, "offset": 15}, ("bars",)),
    SyncCallCase(
        "index_bars",
        {"symbol": "399001", "frequency": "5m", "start": 20, "offset": 15, "market": 0},
        ("index_bars",),
    ),
    SyncCallCase("minutes", {"symbol": "sz000001", "date": "2017-10-10"}, ("minutes",)),
    SyncCallCase("minute", {"symbol": "000001"}, ("minutes",)),
    SyncCallCase("transaction", {"symbol": "600036", "start": 1, "offset": 1}, ("transaction",)),
    SyncCallCase(
        "transactions",
        {"symbol": "600036", "date": 20170209, "start": 20, "offset": 15},
        ("transactions",),
    ),
    SyncCallCase("finance", {"symbol": "600036"}, ("finance",)),
    SyncCallCase("block", {"block_file": "block_zs.dat"}, ("block_info_meta",)),
    SyncCallCase("xdxr", {"symbol": "600036"}, ("xdxr",)),
    SyncCallCase("f10_categories", {"symbol": "600036"}, ("f10_categories",)),
    SyncCallCase(
        "f10_content",
        {"symbol": "600036", "name": "最新提示"},
        ("f10_categories", "f10_content"),
    ),
)


@pytest.mark.parametrize("case", SYNC_CALL_CASES, ids=lambda case: case.method)
def test_sync_client_executes_every_business_api(case: SyncCallCase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients_module, "is_trading_session", lambda: True)
    client, protocol, _ = _matrix_client()

    getattr(client, case.method)(**case.kwargs)

    assert tuple(api for api, _ in protocol.encode_calls) == case.protocol_apis


FREQUENCY_CASES = tuple((frequency, frequency) for frequency in range(12)) + tuple(FREQUENCY_ALIASES.items()) + (("DAY", 9),)


@pytest.mark.parametrize(("frequency", "expected"), FREQUENCY_CASES)
def test_bars_accepts_every_frequency_and_alias(frequency: int | str, expected: int) -> None:
    client, protocol, transport = _matrix_client()

    client.bars("600036", frequency=frequency, start=0, offset=1)

    assert protocol.encode_calls[-1][1]["frequency"] == expected
    assert transport.contexts[-1].params["frequency"] == expected


def test_frequency_matrix_covers_all_wire_values() -> None:
    assert {expected for _, expected in FREQUENCY_CASES} == set(range(12))
    assert set(FREQUENCY_ALIASES) <= {value for value, _ in FREQUENCY_CASES if isinstance(value, str)}


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("bars", {"symbol": "600036", "frequency": 9, "start": 0, "offset": 1}),
        ("bars", {"symbol": "600036", "frequency": 9, "start": 20, "offset": 800}),
        ("index_bars", {"symbol": "000001", "frequency": 9, "start": 0, "offset": 1}),
        ("index_bars", {"symbol": "399001", "frequency": 9, "start": 20, "offset": 800}),
        ("transaction", {"symbol": "600036", "start": 0, "offset": 1}),
        ("transaction", {"symbol": "600036", "start": 20, "offset": 1800}),
        ("transactions", {"symbol": "600036", "date": "20170209", "start": 0, "offset": 1}),
        ("transactions", {"symbol": "600036", "date": "20170209", "start": 20, "offset": 2000}),
    ],
)
def test_window_boundary_matrix(method: str, kwargs: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients_module, "is_trading_session", lambda: True)
    client, protocol, _ = _matrix_client()

    getattr(client, method)(**kwargs)

    encoded = protocol.encode_calls[-1][1]
    assert encoded["start"] == kwargs["start"]
    assert encoded["count"] == kwargs["offset"]


@pytest.mark.parametrize(("value", "expected"), [("20171010", "20171010"), (20171010, "20171010"), ("2017-10-10", "20171010")])
@pytest.mark.parametrize("method", ["minutes", "transactions"])
def test_date_representation_matrix(method: str, value: str | int, expected: str) -> None:
    client, protocol, transport = _matrix_client()
    kwargs: dict[str, object] = {"symbol": "600036", "date": value}

    getattr(client, method)(**kwargs)

    assert transport.contexts[-1].params["date"] == expected
    assert protocol.encode_calls[-1][1]["date"] == expected


@pytest.mark.parametrize(
    ("symbol", "market", "code"),
    [
        ("600036", 1, "600036"),
        ("sh600036", 1, "600036"),
        ("SH600036", 1, "600036"),
        ("000001", 0, "000001"),
        ("sz000001", 0, "000001"),
        ("430090", 2, "430090"),
        ("bj430090", 2, "430090"),
    ],
)
def test_symbol_market_prefix_matrix(symbol: str, market: int, code: str) -> None:
    client, protocol, _ = _matrix_client()

    client.bars(symbol, frequency=9, offset=1)

    assert protocol.encode_calls[-1][1]["market"] == market
    assert protocol.encode_calls[-1][1]["code"] == code


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "error"),
    [
        ("stock_count", (3,), {}, UnsupportedMarketError),
        ("stocks", (2,), {}, UnsupportedMarketError),
        ("quotes", (123,), {}, InvalidSymbolError),
        ("quotes", (["600036", 1],), {}, InvalidSymbolError),
        ("bars", ("",), {}, InvalidSymbolError),
        ("bars", ("600036",), {"frequency": 12}, InvalidFrequencyError),
        ("bars", ("600036",), {"start": -1}, ValueError),
        ("bars", ("600036",), {"offset": 0}, ValueError),
        ("bars", ("600036",), {"offset": 801}, ValueError),
        ("index_bars", ("",), {}, InvalidSymbolError),
        ("index_bars", ("000001",), {"start": -1}, ValueError),
        ("index_bars", ("000001",), {"offset": 801}, ValueError),
        ("minutes", ("600036", "2017/10/10"), {}, InvalidDateError),
        ("minutes", ("430090", "20171010"), {}, UnsupportedMarketError),
        ("transaction", ("600036",), {"offset": 0}, ValueError),
        ("transaction", ("600036",), {"offset": 1801}, ValueError),
        ("transactions", ("600036", "20170209"), {"offset": 0}, ValueError),
        ("transactions", ("600036", "20170209"), {"offset": 2001}, ValueError),
        ("transactions", ("430090", "20170209"), {}, UnsupportedMarketError),
        ("finance", ("430090",), {}, UnsupportedMarketError),
        ("xdxr", ("430090",), {}, UnsupportedMarketError),
        ("f10_categories", ("430090",), {}, UnsupportedMarketError),
        ("f10_content", ("600036", ""), {}, UnknownF10CategoryError),
    ],
)
def test_invalid_parameter_equivalence_matrix(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clients_module, "is_trading_session", lambda: True)
    client, _, _ = _matrix_client()

    with pytest.raises(error):
        getattr(client, method)(*args, **kwargs)


def test_transaction_session_state_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _matrix_client()
    monkeypatch.setattr(clients_module, "is_trading_session", lambda: False)

    with pytest.raises(OutsideTradingSessionError):
        client.transaction("600036", offset=1)


class AsyncDispatchRecorder:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def call(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args, kwargs))
            if name == "stock_count":
                return 1
            if name == "finance":
                return {"code": "600036"}
            if name == "f10_content":
                return "content"
            return [{"api": name}]

        return call

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False


@dataclass(frozen=True)
class AsyncCallCase:
    method: str
    args: tuple[object, ...]
    kwargs: dict[str, object]
    sync_method: str
    sync_args: tuple[object, ...]
    sync_kwargs: dict[str, object]


ASYNC_CALL_CASES = (
    AsyncCallCase("request", ("stock_count",), {"market": 1}, "stock_count", (), {"market": 1}),
    AsyncCallCase("stock_count", (1,), {}, "stock_count", (1,), {}),
    AsyncCallCase("stocks", (1,), {}, "stocks", (1,), {}),
    AsyncCallCase("quotes", (["600036", "000001"],), {}, "quotes", (["600036", "000001"],), {}),
    AsyncCallCase("bars", ("600036", "day", 20, 15), {}, "bars", ("600036", "day", 20, 15), {}),
    AsyncCallCase("minutes", ("600036", "2017-10-10"), {}, "minutes", ("600036", "2017-10-10"), {}),
    AsyncCallCase("minute", ("600036",), {}, "minute", ("600036",), {}),
    AsyncCallCase("transaction", ("600036", 20, 15), {}, "transaction", ("600036", 20, 15), {}),
    AsyncCallCase(
        "transactions",
        ("600036", "20170209", 20, 15),
        {},
        "transactions",
        ("600036", "20170209", 20, 15),
        {},
    ),
    AsyncCallCase("finance", ("600036",), {}, "finance", ("600036",), {}),
    AsyncCallCase("xdxr", ("600036",), {}, "xdxr", ("600036",), {}),
    AsyncCallCase(
        "index_bars",
        ("000001", "day", 20, 15, 1),
        {},
        "index_bars",
        ("000001", "day", 20, 15, 1),
        {},
    ),
    AsyncCallCase("block", ("block_zs.dat",), {}, "block", ("block_zs.dat",), {}),
    AsyncCallCase("f10_categories", ("600036",), {}, "f10_categories", ("600036",), {}),
    AsyncCallCase(
        "f10_content",
        ("600036", "最新提示"),
        {},
        "f10_content",
        ("600036", "最新提示"),
        {},
    ),
)


@pytest.mark.parametrize("case", ASYNC_CALL_CASES, ids=lambda case: case.method)
def test_async_dispatch_matrix_covers_every_supported_business_method(case: AsyncCallCase) -> None:
    recorder = AsyncDispatchRecorder()
    client = AsyncClient(sync_client=recorder)

    asyncio.run(getattr(client, case.method)(*case.args, **case.kwargs))

    assert recorder.calls == [(case.sync_method, case.sync_args, case.sync_kwargs)]


class FacadeRecorder:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False

    def quotes(self, symbol=None):
        return [{"code": "600036", "price": 10.0, "vol": 1}]

    def bars(self, symbol, frequency=9, start=0, offset=800):
        return [
            {
                "open": 10.0,
                "close": 10.1,
                "high": 10.2,
                "low": 9.9,
                "vol": 1,
                "amount": 10.0,
                "datetime": "2019-07-05 15:00",
            }
        ]

    def stock_count(self, market):
        return 1

    def stocks(self, market):
        return [{"code": "600036", "name": "招商银行"}]

    def minutes(self, symbol, date):
        return [{"date": f"{date} 09:30", "price": 10.0}]

    def transaction(self, symbol, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def transactions(self, symbol, date, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def f10_categories(self, symbol):
        return [{"name": "最新提示", "filename": "600036.txt", "start": 0, "length": 4}]

    def f10_content(self, symbol, name):
        return "content"

    def xdxr(self, symbol):
        return [{"year": 2026, "category": 1}]

    def finance(self, symbol):
        return {"code": symbol, "liutongguben": 1.0}

    def index_bars(self, symbol, frequency=9, start=0, offset=800, market=None):
        return self.bars(symbol, frequency, start, offset)

    def block(self, tofile):
        return [{"blockname": "测试", "code": "600036"}]


@dataclass(frozen=True)
class FacadeCase:
    method: str
    kwargs: dict[str, object]
    result_type: type | tuple[type, ...]


FACADE_CASES = (
    FacadeCase("quotes", {"symbol": ["600036", "000001"]}, pd.DataFrame),
    FacadeCase("bars", {"symbol": "600036", "frequency": "day", "start": 20, "offset": 900}, pd.DataFrame),
    FacadeCase("stock_count", {"market": 2}, int),
    FacadeCase("stocks", {"market": 1}, pd.DataFrame),
    FacadeCase("stock_all", {}, pd.DataFrame),
    FacadeCase("minute", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("minutes", {"symbol": "600036", "date": "2017-10-10"}, pd.DataFrame),
    FacadeCase("transaction", {"symbol": "600036", "start": 20, "offset": 15}, pd.DataFrame),
    FacadeCase(
        "transactions",
        {"symbol": "600036", "date": "20170209", "start": 20, "offset": 15},
        pd.DataFrame,
    ),
    FacadeCase("F10C", {"symbol": "600036"}, list),
    FacadeCase("F10", {"symbol": "600036", "name": "最新提示"}, str),
    FacadeCase("xdxr", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("finance", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("index_bars", {"symbol": "000001", "frequency": "5m", "offset": 1}, pd.DataFrame),
    FacadeCase("index", {"symbol": "399001", "frequency": "day", "market": 0, "offset": 1}, pd.DataFrame),
    FacadeCase("block", {"tofile": "block_zs.dat"}, pd.DataFrame),
    FacadeCase(
        "get_k_data",
        {"code": "600036", "start_date": "2019-07-03", "end_date": "2019-07-10"},
        pd.DataFrame,
    ),
    FacadeCase("k", {"symbol": "600036", "begin": "2019-07-03", "end": "2019-07-10"}, pd.DataFrame),
    FacadeCase("ohlc", {"symbol": "600036", "begin": "2019-07-03", "end": "2019-07-10"}, pd.DataFrame),
)


@pytest.mark.parametrize("case", FACADE_CASES, ids=lambda case: case.method)
def test_next_facade_executes_every_business_api(case: FacadeCase) -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    result = getattr(client, case.method)(**case.kwargs)

    assert isinstance(result, case.result_type)


def test_next_facade_lifecycle_and_traffic_contract() -> None:
    recorder = FacadeRecorder()
    client = NextStdQuotes(engine_client=recorder)

    assert client.closed is False
    assert client.traffic() is None
    client.close()
    assert client.closed is True
    client.reconnect()
    assert client.closed is False


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("stock_count", {"market": 3}),
        ("stocks", {"market": 2}),
        ("minutes", {"symbol": "430090", "date": "20171010"}),
        ("transactions", {"symbol": "430090", "date": "20171010"}),
        ("F10C", {"symbol": "430090"}),
        ("F10", {"symbol": "430090", "name": "最新提示"}),
    ],
)
def test_next_facade_translates_market_validation_errors(method: str, kwargs: dict[str, object]) -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    with pytest.raises(MootdxValidationException):
        getattr(client, method)(**kwargs)
