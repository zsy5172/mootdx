from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

import pandas as pd
import pytest

from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextExtQuotes
from mootdx.quotes import NextStdQuotes
from mootdx_next.api.ex_pandas import ExPandasClient
from mootdx_next.api.pandas import AsyncPandasClient
from mootdx_next.api.pandas import PandasClient
from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.bse import BseRegistry
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.financial import AsyncFinancialFileClient
from mootdx_next.financial import FinancialFileClient
from mootdx_next.financial import FinancialReader
from mootdx_next.localfiles import BlockReader
from mootdx_next.localfiles import CustomerBlockReader
from mootdx_next.localfiles import ExtBarReader
from mootdx_next.localfiles import StdDailyBarReader
from mootdx_next.localfiles import StdLCMinBarReader
from mootdx_next.localfiles import StdMinBarReader
from mootdx_next.models import RequestContext
from mootdx_next.models import ResponseEnvelope
from mootdx_next.models import ServerEndpoint
from mootdx_next.models import TransportMetrics
from mootdx_next.params import FREQUENCY_ALIASES
from mootdx_next.securities import SecurityRegistry
from mootdx_next.trading_calendar import TradingCalendarRegistry
from mootdx_next.reader import ExtReader
from mootdx_next.reader import Reader
from mootdx_next.reader import StdReader
from tests.core_engine.test_sync_client_stock_apis import RecordingConnectionPool
from tests.core_engine.test_sync_client_stock_apis import RecordingScheduler


SYNC_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "request",
    "stock_count",
    "stock_page",
    "stocks",
    "securities",
    "security",
    "stock_codes",
    "etf_codes",
    "index_codes",
    "quotes",
    "quotes_all",
    "limit_prices",
    "price_limit",
    "bars",
    "bars_until",
    "bars_all",
    "minute_bars_241",
    "minute_bars_241_until",
    "minute_bars_241_all",
    "index_bars",
    "index_bars_until",
    "index_bars_all",
    "minutes",
    "minute",
    "call_auction",
    "transaction",
    "transaction_all",
    "transactions",
    "transactions_day",
    "iter_transactions",
    "iter_transaction_history",
    "trading_days",
    "is_trading_day",
    "finance",
    "block",
    "block_file_raw",
    "report_file",
    "zhb_files",
    "tdx_block_indexes",
    "tdx_block_aliases",
    "block_catalog",
    "block_members",
    "block_members_all",
    "tdx_block_base",
    "fund_flows",
    "block_with_index",
    "sp_blocks",
    "tdx_industries",
    "ipo_subscriptions",
    "stock_statistics",
    "stock_statistics2",
    "gbbq_all",
    "gbbq",
    "xdxr",
    "xdxr_by_date",
    "iter_xdxr",
    "equity_at",
    "market_value",
    "turnover",
    "adjustment_factors",
    "f10_categories",
    "f10_content",
    "f10_content_range",
}

ASYNC_PUBLIC_API = {
    "closed",
    "close",
    "reconnect",
    "request",
    "stock_count",
    "stock_page",
    "stocks",
    "securities",
    "security",
    "stock_codes",
    "etf_codes",
    "index_codes",
    "quotes",
    "quotes_all",
    "limit_prices",
    "price_limit",
    "bars",
    "bars_until",
    "bars_all",
    "minute_bars_241",
    "minute_bars_241_until",
    "minute_bars_241_all",
    "minutes",
    "minute",
    "call_auction",
    "transaction",
    "transaction_all",
    "transactions",
    "transactions_day",
    "iter_transactions",
    "iter_transaction_history",
    "trading_days",
    "is_trading_day",
    "finance",
    "gbbq_all",
    "gbbq",
    "xdxr",
    "xdxr_by_date",
    "iter_xdxr",
    "equity_at",
    "market_value",
    "turnover",
    "adjustment_factors",
    "index_bars",
    "index_bars_until",
    "index_bars_all",
    "block",
    "block_file_raw",
    "report_file",
    "zhb_files",
    "tdx_block_indexes",
    "tdx_block_aliases",
    "block_catalog",
    "block_members",
    "block_members_all",
    "tdx_block_base",
    "fund_flows",
    "block_with_index",
    "sp_blocks",
    "tdx_industries",
    "ipo_subscriptions",
    "stock_statistics",
    "stock_statistics2",
    "f10_categories",
    "f10_content",
    "f10_content_range",
}

PANDAS_PUBLIC_API = {
    "closed",
    "raw_client",
    "close",
    "reconnect",
    "metrics",
    "traffic",
    "quotes",
    "quotes_all",
    "limit_prices",
    "price_limit",
    "bars",
    "bars_until",
    "bars_all",
    "minute_bars_241",
    "minute_bars_241_until",
    "minute_bars_241_all",
    "stock_count",
    "stock_page",
    "stocks",
    "securities",
    "security",
    "stock_codes",
    "etf_codes",
    "index_codes",
    "stock_all",
    "minute",
    "call_auction",
    "minutes",
    "transaction",
    "transaction_all",
    "transactions",
    "transactions_day",
    "iter_transactions",
    "iter_transaction_history",
    "trading_days",
    "is_trading_day",
    "f10_categories",
    "f10_content",
    "f10_content_range",
    "F10C",
    "F10",
    "gbbq_all",
    "gbbq",
    "xdxr",
    "xdxr_by_date",
    "iter_xdxr",
    "equity_at",
    "market_value",
    "turnover",
    "adjustment_factors",
    "finance",
    "index_bars",
    "index_bars_until",
    "index_bars_all",
    "index",
    "block",
    "block_file_raw",
    "report_file",
    "zhb_files",
    "tdx_block_indexes",
    "tdx_block_aliases",
    "block_catalog",
    "block_members",
    "block_members_all",
    "tdx_block_base",
    "fund_flows",
    "block_with_index",
    "sp_blocks",
    "tdx_industries",
    "ipo_subscriptions",
    "stock_statistics",
    "stock_statistics2",
    "get_k_data",
    "k",
    "ohlc",
}


FINANCIAL_CLIENT_PUBLIC_API = {
    "catalog",
    "close",
    "fetch",
    "fetch_and_parse",
    "files",
    "parse",
}

FINANCIAL_READER_PUBLIC_API = {
    "from_bytes",
    "parse_bytes",
    "parse_payload",
    "read",
    "to_data",
    "to_frame",
}

READER_PUBLIC_API = {
    Reader: {"factory"},
    StdReader: {"block", "block_new", "daily", "find_path", "fzline", "minute"},
    ExtReader: {"daily", "find_path", "fzline", "minute"},
    StdDailyBarReader: {"get_df", "get_security_type", "parse_data_by_file", "parse_date", "parse_time", "unpack_records"},
    StdMinBarReader: {"get_df", "parse_data_by_file", "parse_date", "parse_time", "unpack_records"},
    StdLCMinBarReader: {"get_df", "parse_data_by_file", "parse_date", "parse_time", "unpack_records"},
    ExtBarReader: {"get_df", "parse_data_by_file", "parse_date", "parse_time", "unpack_records"},
    BlockReader: {"get_data", "get_df"},
    CustomerBlockReader: {"get_data", "get_df"},
}


def _public_api(owner: type) -> set[str]:
    return {
        name
        for name, value in owner.__dict__.items()
        if not name.startswith("_") and (inspect.isroutine(value) or isinstance(value, property))
    }


def _all_public_callables(owner: type) -> set[str]:
    return {name for name in dir(owner) if not name.startswith("_") and callable(getattr(owner, name))}


def test_public_api_inventory_requires_matrix_updates_for_new_methods() -> None:
    assert _public_api(SyncClient) == SYNC_PUBLIC_API
    assert _public_api(AsyncClient) == ASYNC_PUBLIC_API
    assert _public_api(PandasClient) == PANDAS_PUBLIC_API
    assert _public_api(AsyncPandasClient) == PANDAS_PUBLIC_API
    assert _public_api(NextStdQuotes) == set()
    assert issubclass(NextStdQuotes, PandasClient)
    assert _public_api(NextExtQuotes) == set()
    assert issubclass(NextExtQuotes, ExPandasClient)


def test_financial_and_local_reader_public_api_inventory() -> None:
    assert _all_public_callables(FinancialFileClient) == FINANCIAL_CLIENT_PUBLIC_API
    assert _all_public_callables(AsyncFinancialFileClient) == FINANCIAL_CLIENT_PUBLIC_API
    assert _all_public_callables(FinancialReader) == FINANCIAL_READER_PUBLIC_API

    for owner, expected in READER_PUBLIC_API.items():
        assert _all_public_callables(owner) == expected


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
        if api == "limit_prices":
            return [{"market": 1, "code": "600036", "limit_up": 42.9, "limit_down": 35.1}]
        if api == "block_info_meta":
            return {"size": 0, "hash": ""}
        if api == "f10_categories":
            return [{"name": "最新提示", "filename": "600036.txt", "start": 0, "length": 7}]
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
    class EmptyBseProvider:
        def load(self):
            return []

    client = SyncClient(
        protocol=protocol,
        connection_pool=pool,
        scheduler=scheduler,
        bse_registry=BseRegistry(EmptyBseProvider()),
        security_registry=SecurityRegistry(),
        trading_calendar_registry=TradingCalendarRegistry(),
    )
    return client, protocol, transport


@dataclass(frozen=True)
class SyncCallCase:
    method: str
    kwargs: dict[str, object]
    protocol_apis: tuple[str, ...]


def _never_bar(_row: object) -> bool:
    return False


SYNC_CALL_CASES = (
    SyncCallCase("stock_count", {"market": 0}, ("stock_count",)),
    SyncCallCase("stock_page", {"market": 1, "start": 1000}, ("stock_list_page",)),
    SyncCallCase("stocks", {"market": 1}, ("stock_count",)),
    SyncCallCase("securities", {}, ("stock_count", "stock_count")),
    SyncCallCase("security", {"symbol": "600036"}, ("stock_count", "stock_count")),
    SyncCallCase("stock_codes", {}, ("stock_count", "stock_count")),
    SyncCallCase("etf_codes", {}, ("stock_count", "stock_count")),
    SyncCallCase("index_codes", {}, ("stock_count", "stock_count")),
    SyncCallCase("quotes", {"symbol": ["600036", "sz000001"]}, ("quotes",)),
    SyncCallCase("limit_prices", {"start": 20, "count": 15}, ("limit_prices",)),
    SyncCallCase("price_limit", {"symbol": "600036", "refresh": True}, ("limit_prices",)),
    SyncCallCase("bars", {"symbol": "sh600036", "frequency": "day", "start": 20, "offset": 15}, ("bars",)),
    SyncCallCase(
        "bars_until",
        {"symbol": "sh600036", "predicate": _never_bar, "frequency": "day"},
        ("bars",),
    ),
    SyncCallCase("bars_all", {"symbol": "sh600036", "frequency": "day"}, ("bars",)),
    SyncCallCase("minute_bars_241", {"symbol": "sh600036", "offset": 15}, ("bars",)),
    SyncCallCase(
        "minute_bars_241_until",
        {"symbol": "sh600036", "predicate": _never_bar},
        ("bars",),
    ),
    SyncCallCase("minute_bars_241_all", {"symbol": "sh600036"}, ("bars",)),
    SyncCallCase(
        "index_bars",
        {"symbol": "399001", "frequency": "5m", "start": 20, "offset": 15, "market": 0},
        ("index_bars",),
    ),
    SyncCallCase(
        "index_bars_until",
        {"symbol": "sh000001", "predicate": _never_bar, "frequency": "day", "market": 1},
        ("index_bars",),
    ),
    SyncCallCase(
        "index_bars_all",
        {"symbol": "sh000001", "frequency": "day", "market": 1},
        ("index_bars",),
    ),
    SyncCallCase("minutes", {"symbol": "sz000001", "date": "2017-10-10"}, ("minutes",)),
    SyncCallCase("minute", {"symbol": "000001"}, ("minutes",)),
    SyncCallCase("transaction", {"symbol": "600036", "start": 1, "offset": 1}, ("transaction",)),
    SyncCallCase("transaction_all", {"symbol": "600036"}, ("transaction",)),
    SyncCallCase(
        "transactions",
        {"symbol": "600036", "date": 20170209, "start": 20, "offset": 15},
        ("transactions",),
    ),
    SyncCallCase(
        "transactions_day",
        {"symbol": "600036", "date": 20170209},
        ("transactions",),
    ),
    SyncCallCase(
        "trading_days",
        {"start_date": "20260701", "end_date": "20260730"},
        ("index_bars",),
    ),
    SyncCallCase("is_trading_day", {"date": "20260730"}, ("index_bars",)),
    SyncCallCase("finance", {"symbol": "600036"}, ("finance",)),
    SyncCallCase("block", {"block_file": "block_zs.dat"}, ("block_info_meta",)),
    SyncCallCase("xdxr", {"symbol": "600036"}, ("xdxr",)),
    SyncCallCase("xdxr_by_date", {"symbol": "600036"}, ("xdxr",)),
    SyncCallCase("equity_at", {"symbol": "600036", "as_of": "20260730"}, ("xdxr",)),
    SyncCallCase(
        "market_value",
        {"symbol": "600036", "as_of": "20260730", "price": 39.0},
        ("xdxr",),
    ),
    SyncCallCase(
        "turnover",
        {"symbol": "600036", "as_of": "20260730", "volume": 100},
        ("xdxr",),
    ),
    SyncCallCase("f10_categories", {"symbol": "600036"}, ("f10_categories",)),
    SyncCallCase(
        "f10_content",
        {"symbol": "600036", "name": "最新提示"},
        ("f10_categories", "f10_content"),
    ),
    SyncCallCase(
        "f10_content_range",
        {"symbol": "600036", "filename": "600036.txt", "start": 0, "length": 7},
        ("f10_content",),
    ),
)


@pytest.mark.parametrize("case", SYNC_CALL_CASES, ids=lambda case: case.method)
def test_sync_client_executes_every_business_api(case: SyncCallCase) -> None:
    client, protocol, _ = _matrix_client()

    getattr(client, case.method)(**case.kwargs)

    assert tuple(api for api, _ in protocol.encode_calls) == case.protocol_apis


def test_sync_iter_transactions_executes_when_consumed() -> None:
    client, protocol, _ = _matrix_client()

    chunks = list(
        client.iter_transactions(
            "600036",
            "20170209",
            "20170209",
            include_empty=True,
            trading_days_only=False,
            max_pages=1,
        )
    )

    assert chunks == [("20170209", [])]
    assert [api for api, _ in protocol.encode_calls] == ["transactions"]


def test_sync_iter_transaction_history_executes_when_consumed() -> None:
    client, protocol, _ = _matrix_client()

    iterator = client.iter_transaction_history("600036", before="20170209")

    assert protocol.encode_calls == []
    assert list(iterator) == []
    assert [api for api, _ in protocol.encode_calls] == ["bars"]


def test_sync_iter_xdxr_executes_when_consumed() -> None:
    client, protocol, _ = _matrix_client()

    chunks = list(client.iter_xdxr(["600036"]))

    assert chunks == [("sh600036", [])]
    assert [api for api, _ in protocol.encode_calls] == ["xdxr"]


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
        ("limit_prices", {"start": 0, "count": 1}),
        ("limit_prices", {"start": 20, "count": 2000}),
        ("transaction", {"symbol": "600036", "start": 0, "offset": 1}),
        ("transaction", {"symbol": "600036", "start": 20, "offset": 1800}),
        ("transactions", {"symbol": "600036", "date": "20170209", "start": 0, "offset": 1}),
        ("transactions", {"symbol": "600036", "date": "20170209", "start": 20, "offset": 2000}),
    ],
)
def test_window_boundary_matrix(method: str, kwargs: dict[str, object]) -> None:
    client, protocol, _ = _matrix_client()

    getattr(client, method)(**kwargs)

    encoded = protocol.encode_calls[-1][1]
    assert encoded["start"] == kwargs["start"]
    expected_count = kwargs["count"] if method == "limit_prices" else kwargs["offset"]
    assert encoded["count"] == expected_count


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
        ("SH.600036", 1, "600036"),
        ("000001", 0, "000001"),
        ("sz000001", 0, "000001"),
        ("sz#000001", 0, "000001"),
        ("430090", 2, "430090"),
        ("bj430090", 2, "430090"),
        ("BJ.430090", 2, "430090"),
        ("920001", 2, "920001"),
    ],
)
def test_symbol_market_prefix_matrix(symbol: str, market: int, code: str) -> None:
    client, protocol, _ = _matrix_client()

    client.bars(symbol, frequency=9, offset=1)

    assert protocol.encode_calls[-1][1]["market"] == market
    assert protocol.encode_calls[-1][1]["code"] == code


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "expected_apis"),
    [
        ("minutes", ("bj430090", "20171010"), {}, ("minutes",)),
        ("call_auction", ("bj430090",), {}, ("call_auction",)),
        ("transaction", ("bj430090",), {"offset": 1}, ("transaction",)),
        ("transactions", ("bj430090", "20171010"), {"offset": 1}, ("transactions",)),
        ("finance", ("bj430090",), {}, ("finance",)),
        ("f10_categories", ("bj430090",), {}, ("f10_categories",)),
        (
            "f10_content",
            ("bj430090", "最新提示"),
            {},
            ("f10_categories", "f10_content"),
        ),
        (
            "f10_content_range",
            ("bj430090", "430090.txt", 0, 7),
            {},
            ("f10_content",),
        ),
    ],
)
def test_bse_business_apis_preserve_market_context(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    expected_apis: tuple[str, ...],
) -> None:
    client, protocol, transport = _matrix_client()

    getattr(client, method)(*args, **kwargs)

    assert tuple(api for api, _ in protocol.encode_calls) == expected_apis
    assert all(call_kwargs["market"] == 2 for _, call_kwargs in protocol.encode_calls)
    assert all(context.params["market"] == 2 for context in transport.contexts)


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "error"),
    [
        ("stock_count", (3,), {}, UnsupportedMarketError),
        ("stocks", (3,), {}, UnsupportedMarketError),
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
        ("limit_prices", (), {"start": -1}, ValueError),
        ("limit_prices", (), {"start": 65536}, ValueError),
        ("limit_prices", (), {"count": 0}, ValueError),
        ("limit_prices", (), {"count": 2001}, ValueError),
        ("price_limit", ("",), {}, InvalidSymbolError),
        ("price_limit", ("60003A",), {}, InvalidSymbolError),
        ("minutes", ("600036", "2017/10/10"), {}, InvalidDateError),
        ("transaction", ("600036",), {"offset": 0}, ValueError),
        ("transaction", ("600036",), {"offset": 1801}, ValueError),
        ("transactions", ("600036", "20170209"), {"offset": 0}, ValueError),
        ("transactions", ("600036", "20170209"), {"offset": 2001}, ValueError),
        ("f10_content", ("600036", ""), {}, UnknownF10CategoryError),
    ],
)
def test_invalid_parameter_equivalence_matrix(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    client, _, _ = _matrix_client()

    with pytest.raises(error):
        getattr(client, method)(*args, **kwargs)


def test_transaction_does_not_consult_session_utility(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _matrix_client()
    monkeypatch.setattr(
        "mootdx_next.session.is_trading_session",
        lambda: pytest.fail("transaction must not consult the local session clock"),
    )

    assert client.transaction("600036", offset=1) == []


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
            if name == "security":
                return {"market": 1, "code": "600036", "symbol": "sh600036"}
            if name == "equity_at":
                return {"symbol": "sh600036", "float_shares": 1000}
            if name == "market_value":
                return {"symbol": "sh600036", "float_market_value": 39000}
            if name == "xdxr_by_date":
                return {"2026-07-30": [{"category": 1}]}
            if name == "turnover":
                return 1.0
            if name == "price_limit":
                return {
                    "market": 1,
                    "code": "600036",
                    "limit_up": 42.9,
                    "limit_down": 35.1,
                    "source": "calculated",
                }
            if name == "f10_content":
                return "content"
            if name == "f10_content_range":
                return b"content"
            if name == "trading_days":
                return ("20260730",)
            if name == "is_trading_day":
                return True
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
    AsyncCallCase("request", ("stock_count",), {"market": 1}, "request", ("stock_count",), {"market": 1}),
    AsyncCallCase("stock_count", (1,), {}, "stock_count", (1,), {}),
    AsyncCallCase("stock_page", (1, 1000, True), {}, "stock_page", (1, 1000, True), {}),
    AsyncCallCase("stocks", (1,), {}, "stocks", (1,), {}),
    AsyncCallCase("securities", (True,), {}, "securities", (True,), {}),
    AsyncCallCase("security", ("600036", True), {}, "security", ("600036", True), {}),
    AsyncCallCase("stock_codes", (True,), {}, "stock_codes", (True,), {}),
    AsyncCallCase("etf_codes", (True,), {}, "etf_codes", (True,), {}),
    AsyncCallCase("index_codes", (True,), {}, "index_codes", (True,), {}),
    AsyncCallCase("quotes", (["600036", "000001"],), {}, "quotes", (["600036", "000001"],), {}),
    AsyncCallCase("limit_prices", (20, 15), {}, "limit_prices", (20, 15), {}),
    AsyncCallCase("price_limit", ("600036", True), {}, "price_limit", ("600036", True), {}),
    AsyncCallCase("bars", ("600036", "day", 20, 15), {}, "bars", ("600036", "day", 20, 15), {}),
    AsyncCallCase(
        "bars_until",
        ("600036", _never_bar, "day", 400, 2),
        {},
        "bars_until",
        ("600036", _never_bar, "day", 400, 2),
        {},
    ),
    AsyncCallCase(
        "bars_all",
        ("600036", "day", 400, 2),
        {},
        "bars_all",
        ("600036", "day", 400, 2),
        {},
    ),
    AsyncCallCase(
        "minute_bars_241",
        ("600036", 20, 15),
        {"transaction_max_pages": 2},
        "minute_bars_241",
        ("600036", 20, 15),
        {"transaction_max_pages": 2},
    ),
    AsyncCallCase(
        "minute_bars_241_until",
        ("600036", _never_bar, 400, 2),
        {"transaction_max_pages": 3},
        "minute_bars_241_until",
        ("600036", _never_bar, 400, 2),
        {"transaction_max_pages": 3},
    ),
    AsyncCallCase(
        "minute_bars_241_all",
        ("600036", 400, 2),
        {"transaction_max_pages": 3},
        "minute_bars_241_all",
        ("600036", 400, 2),
        {"transaction_max_pages": 3},
    ),
    AsyncCallCase("minutes", ("600036", "2017-10-10"), {}, "minutes", ("600036", "2017-10-10"), {}),
    AsyncCallCase("minute", ("600036",), {}, "minute", ("600036",), {}),
    AsyncCallCase("transaction", ("600036", 20, 15), {}, "transaction", ("600036", 20, 15), {}),
    AsyncCallCase(
        "transaction_all",
        ("600036", 900, 2),
        {},
        "transaction_all",
        ("600036", 900, 2),
        {},
    ),
    AsyncCallCase(
        "transactions",
        ("600036", "20170209", 20, 15),
        {},
        "transactions",
        ("600036", "20170209", 20, 15),
        {},
    ),
    AsyncCallCase(
        "transactions_day",
        ("600036", "20170209", 1000, 2),
        {},
        "transactions_day",
        ("600036", "20170209", 1000, 2),
        {},
    ),
    AsyncCallCase(
        "trading_days",
        ("20260701", "20260730"),
        {"refresh": True},
        "trading_days",
        ("20260701", "20260730"),
        {"refresh": True},
    ),
    AsyncCallCase(
        "is_trading_day",
        ("20260730",),
        {"refresh": True},
        "is_trading_day",
        ("20260730",),
        {"refresh": True},
    ),
    AsyncCallCase("finance", ("600036",), {}, "finance", ("600036",), {}),
    AsyncCallCase("gbbq_all", (True,), {}, "gbbq_all", (True,), {}),
    AsyncCallCase(
        "gbbq",
        ("600036", True),
        {"fallback": False},
        "gbbq",
        ("600036", True),
        {"fallback": False},
    ),
    AsyncCallCase("xdxr", ("600036",), {}, "xdxr", ("600036",), {}),
    AsyncCallCase(
        "xdxr_by_date",
        ("600036", (1, 11)),
        {},
        "xdxr_by_date",
        ("600036", (1, 11)),
        {},
    ),
    AsyncCallCase(
        "equity_at",
        ("600036", "20260730"),
        {},
        "equity_at",
        ("600036", "20260730"),
        {},
    ),
    AsyncCallCase(
        "market_value",
        ("600036", "20260730", 39.0),
        {},
        "market_value",
        ("600036", "20260730", 39.0),
        {},
    ),
    AsyncCallCase(
        "turnover",
        ("600036", "20260730", 100),
        {"volume_unit": "lots"},
        "turnover",
        ("600036", "20260730", 100),
        {"volume_unit": "lots"},
    ),
    AsyncCallCase(
        "adjustment_factors",
        ("600036",),
        {},
        "adjustment_factors",
        ("600036",),
        {},
    ),
    AsyncCallCase(
        "index_bars",
        ("000001", "day", 20, 15, 1),
        {},
        "index_bars",
        ("000001", "day", 20, 15, 1),
        {},
    ),
    AsyncCallCase(
        "index_bars_until",
        ("sh000001", _never_bar, "day", 1, 400, 2),
        {},
        "index_bars_until",
        ("sh000001", _never_bar, "day", 1, 400, 2),
        {},
    ),
    AsyncCallCase(
        "index_bars_all",
        ("sh000001", "day", 1, 400, 2),
        {},
        "index_bars_all",
        ("sh000001", "day", 1, 400, 2),
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
    AsyncCallCase(
        "f10_content_range",
        ("600036", "600036.txt", 0, 7),
        {},
        "f10_content_range",
        ("600036", "600036.txt", 0, 7),
        {},
    ),
)


@pytest.mark.parametrize("case", ASYNC_CALL_CASES, ids=lambda case: case.method)
def test_async_dispatch_matrix_covers_every_supported_business_method(case: AsyncCallCase) -> None:
    recorder = AsyncDispatchRecorder()
    client = AsyncClient(sync_client=recorder)

    asyncio.run(getattr(client, case.method)(*case.args, **case.kwargs))

    assert recorder.calls == [(case.sync_method, case.sync_args, case.sync_kwargs)]


def test_async_iter_transactions_dispatches_each_consumed_day() -> None:
    recorder = AsyncDispatchRecorder()
    client = AsyncClient(sync_client=recorder)

    async def collect():
        return [
            chunk
            async for chunk in client.iter_transactions(
                "600036",
                "20170209",
                "20170209",
                trading_days_only=False,
                max_pages=1,
            )
        ]

    chunks = asyncio.run(collect())

    assert chunks
    assert recorder.calls == [
        ("transactions_day", ("600036", "20170209", 2000, 1), {})
    ]


def test_async_iter_xdxr_dispatches_each_consumed_symbol() -> None:
    recorder = AsyncDispatchRecorder()
    client = AsyncClient(sync_client=recorder)

    async def collect():
        return [chunk async for chunk in client.iter_xdxr(["600036"])]

    chunks = asyncio.run(collect())

    assert chunks[0][0] == "sh600036"
    assert recorder.calls == [("xdxr", ("sh600036",), {})]


class FacadeRecorder:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def reconnect(self) -> None:
        self.closed = False

    def quotes(self, symbol=None):
        return [{"code": "600036", "price": 10.0, "vol": 1}]

    def limit_prices(self, start=0, count=2000):
        return [{"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14}]

    def price_limit(self, symbol, refresh=False):
        return {
            "market": 1,
            "code": "600036",
            "limit_up": 42.9,
            "limit_down": 35.1,
            "source": "calculated",
        }

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

    def bars_until(self, symbol, predicate, frequency=9, page_size=800, max_pages=None):
        return self.bars(symbol, frequency, 0, page_size)

    def bars_all(self, symbol, frequency=9, page_size=800, max_pages=None):
        return self.bars(symbol, frequency, 0, page_size)

    def minute_bars_241(
        self,
        symbol,
        start=0,
        offset=800,
        transaction_max_pages=None,
    ):
        return self.bars(symbol, 8, start, offset)

    def minute_bars_241_until(
        self,
        symbol,
        predicate,
        page_size=800,
        max_pages=None,
        transaction_max_pages=None,
    ):
        return self.bars(symbol, 8, 0, page_size)

    def minute_bars_241_all(
        self,
        symbol,
        page_size=800,
        max_pages=None,
        transaction_max_pages=None,
    ):
        return self.bars(symbol, 8, 0, page_size)

    def stock_count(self, market):
        return 1

    def stock_page(self, market, start=0, refresh=False):
        return self.stocks(market, refresh=refresh)

    def stocks(self, market, refresh=False):
        code = {0: "000001", 1: "600036", 2: "920001"}[market]
        return [{"market": market, "code": code, "name": "测试证券"}]

    def securities(self, refresh=False):
        return [
            {
                "market": 1,
                "code": "600036",
                "symbol": "sh600036",
                "name": "招商银行",
                "security_type": "stock",
            }
        ]

    def security(self, symbol, refresh=False):
        return self.securities(refresh=refresh)[0] if symbol == "600036" else None

    def stock_codes(self, refresh=False):
        return ["sh600036"]

    def etf_codes(self, refresh=False):
        return ["sh510300"]

    def index_codes(self, refresh=False):
        return ["sh000001"]

    def minutes(self, symbol, date):
        return [{"date": f"{date} 09:30", "price": 10.0}]

    def transaction(self, symbol, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def transaction_all(self, symbol, page_size=1800, max_pages=None):
        return self.transaction(symbol, 0, page_size)

    def transactions(self, symbol, date, start=0, offset=800):
        return [{"time": "09:30", "price": 10.0, "vol": 1}]

    def transactions_day(self, symbol, date, page_size=2000, max_pages=None):
        return self.transactions(symbol, date, 0, page_size)

    def iter_transactions(
        self,
        symbol,
        start_date,
        end_date,
        include_empty=False,
        trading_days_only=True,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
    ):
        yield str(start_date), self.transactions_day(symbol, start_date, page_size, max_pages)

    def iter_transaction_history(
        self,
        symbol,
        before=None,
        include_today=False,
        include_empty=False,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
    ):
        date = "20170209" if before is None else str(before).replace("-", "")
        yield date, self.transactions_day(symbol, date, page_size, max_pages)

    def trading_days(self, start_date=None, end_date=None, refresh=False):
        return ["20260730"]

    def is_trading_day(self, date, refresh=False):
        return True

    def f10_categories(self, symbol):
        return [{"name": "最新提示", "filename": "600036.txt", "start": 0, "length": 4}]

    def f10_content(self, symbol, name):
        return "content"

    def f10_content_range(self, symbol, filename, start, length):
        return b"content"

    def xdxr(self, symbol):
        return [{"year": 2026, "category": 1}]

    def xdxr_by_date(self, symbol, categories=None):
        return {"2026-07-30": [{"year": 2026, "category": 1}]}

    def gbbq_all(self, refresh=False):
        return [{"market": 1, "code": "600036", "category": 1, "source": "gbbq.zip"}]

    def gbbq(self, symbol, refresh=False, fallback=True):
        return self.gbbq_all(refresh=refresh)

    def iter_xdxr(self, symbols=None, refresh=False, retries=1):
        yield "sh600036", self.xdxr("sh600036")

    def equity_at(self, symbol, as_of):
        return {"symbol": "sh600036", "float_shares": 1000, "total_shares": 2000}

    def market_value(self, symbol, as_of, price):
        return {
            "symbol": "sh600036",
            "price": price,
            "float_market_value": float(price) * 1000,
        }

    def turnover(self, symbol, as_of, volume, volume_unit="shares"):
        return 10.0

    def adjustment_factors(self, symbol):
        return [
            {
                "datetime": "2026-07-30 15:00",
                "qfq_mul": 1.0,
                "qfq_add": 0.0,
                "hfq_mul": 1.0,
                "hfq_add": 0.0,
            }
        ]

    def finance(self, symbol):
        return {"code": symbol, "liutongguben": 1.0}

    def index_bars(self, symbol, frequency=9, start=0, offset=800, market=None):
        return self.bars(symbol, frequency, start, offset)

    def index_bars_until(
        self,
        symbol,
        predicate,
        frequency=9,
        market=None,
        page_size=800,
        max_pages=None,
    ):
        return self.index_bars(symbol, frequency, 0, page_size, market)

    def index_bars_all(
        self,
        symbol,
        frequency=9,
        market=None,
        page_size=800,
        max_pages=None,
    ):
        return self.index_bars(symbol, frequency, 0, page_size, market)

    def block(self, tofile):
        return [{"blockname": "测试", "code": "600036"}]


@dataclass(frozen=True)
class FacadeCase:
    method: str
    kwargs: dict[str, object]
    result_type: type | tuple[type, ...]


FACADE_CASES = (
    FacadeCase("quotes", {"symbol": ["600036", "000001"]}, pd.DataFrame),
    FacadeCase("limit_prices", {"start": 0, "count": 1}, pd.DataFrame),
    FacadeCase("price_limit", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("bars", {"symbol": "600036", "frequency": "day", "start": 20, "offset": 900}, pd.DataFrame),
    FacadeCase("bars_until", {"symbol": "600036", "predicate": _never_bar}, pd.DataFrame),
    FacadeCase("bars_all", {"symbol": "600036", "max_pages": 1}, pd.DataFrame),
    FacadeCase("minute_bars_241", {"symbol": "600036", "offset": 15}, pd.DataFrame),
    FacadeCase(
        "minute_bars_241_until",
        {"symbol": "600036", "predicate": _never_bar, "max_pages": 1},
        pd.DataFrame,
    ),
    FacadeCase("minute_bars_241_all", {"symbol": "600036", "max_pages": 1}, pd.DataFrame),
    FacadeCase("stock_count", {"market": 2}, int),
    FacadeCase("stock_page", {"market": 1, "start": 1000}, pd.DataFrame),
    FacadeCase("stocks", {"market": 1}, pd.DataFrame),
    FacadeCase("securities", {}, pd.DataFrame),
    FacadeCase("security", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("stock_codes", {}, list),
    FacadeCase("etf_codes", {}, list),
    FacadeCase("index_codes", {}, list),
    FacadeCase("stock_all", {}, pd.DataFrame),
    FacadeCase("minute", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("minutes", {"symbol": "600036", "date": "2017-10-10"}, pd.DataFrame),
    FacadeCase("transaction", {"symbol": "600036", "start": 20, "offset": 15}, pd.DataFrame),
    FacadeCase("transaction_all", {"symbol": "600036", "max_pages": 1}, pd.DataFrame),
    FacadeCase(
        "transactions",
        {"symbol": "600036", "date": "20170209", "start": 20, "offset": 15},
        pd.DataFrame,
    ),
    FacadeCase(
        "transactions_day",
        {"symbol": "600036", "date": "20170209", "max_pages": 1},
        pd.DataFrame,
    ),
    FacadeCase("trading_days", {"start_date": "20260701", "end_date": "20260730"}, list),
    FacadeCase("is_trading_day", {"date": "20260730"}, bool),
    FacadeCase("F10C", {"symbol": "600036"}, list),
    FacadeCase("F10", {"symbol": "600036", "name": "最新提示"}, str),
    FacadeCase(
        "f10_content_range",
        {"symbol": "600036", "filename": "600036.txt", "start": 0, "length": 7},
        bytes,
    ),
    FacadeCase("xdxr", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("xdxr_by_date", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("gbbq_all", {}, pd.DataFrame),
    FacadeCase("gbbq", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("equity_at", {"symbol": "600036", "as_of": "20260730"}, pd.DataFrame),
    FacadeCase(
        "market_value",
        {"symbol": "600036", "as_of": "20260730", "price": 39.0},
        pd.DataFrame,
    ),
    FacadeCase(
        "turnover",
        {"symbol": "600036", "as_of": "20260730", "volume": 100},
        (float, type(None)),
    ),
    FacadeCase("adjustment_factors", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("finance", {"symbol": "600036"}, pd.DataFrame),
    FacadeCase("index_bars", {"symbol": "000001", "frequency": "5m", "offset": 1}, pd.DataFrame),
    FacadeCase(
        "index_bars_until",
        {"symbol": "sh000001", "predicate": _never_bar, "market": 1},
        pd.DataFrame,
    ),
    FacadeCase("index_bars_all", {"symbol": "sh000001", "market": 1, "max_pages": 1}, pd.DataFrame),
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


def test_next_facade_iter_transactions_yields_dataframe_chunks() -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    chunks = list(client.iter_transactions("600036", "20170209", "20170209"))

    assert len(chunks) == 1
    assert chunks[0][0] == "20170209"
    assert isinstance(chunks[0][1], pd.DataFrame)


def test_next_facade_iter_transaction_history_yields_dataframe_chunks() -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    chunks = list(client.iter_transaction_history("600036", before="20170209"))

    assert chunks[0][0] == "20170209"
    assert isinstance(chunks[0][1], pd.DataFrame)


def test_next_facade_iter_xdxr_yields_dataframe_chunks() -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    chunks = list(client.iter_xdxr(["600036"]))

    assert chunks[0][0] == "sh600036"
    assert isinstance(chunks[0][1], pd.DataFrame)


@pytest.mark.parametrize(
    ("method", "kwargs", "result_type"),
    [
        ("stocks", {"market": 2}, pd.DataFrame),
        ("minutes", {"symbol": "bj430090", "date": "20171010"}, pd.DataFrame),
        ("transactions", {"symbol": "bj430090", "date": "20171010"}, pd.DataFrame),
        ("F10C", {"symbol": "bj430090"}, list),
        ("F10", {"symbol": "bj430090", "name": "最新提示"}, str),
    ],
)
def test_next_facade_accepts_bse_business_apis(
    method: str,
    kwargs: dict[str, object],
    result_type: type,
) -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    assert isinstance(getattr(client, method)(**kwargs), result_type)


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("stock_count", {"market": 3}),
        ("stocks", {"market": 3}),
    ],
)
def test_next_facade_translates_market_validation_errors(method: str, kwargs: dict[str, object]) -> None:
    client = NextStdQuotes(engine_client=FacadeRecorder())

    with pytest.raises(MootdxValidationException):
        getattr(client, method)(**kwargs)
