from __future__ import annotations

import asyncio
import math
import struct
import threading
from collections.abc import Callable
from collections.abc import AsyncIterator
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from datetime import datetime
from datetime import date as Date
from datetime import timedelta
from typing import Any

from mootdx_next.bse import BseProvider
from mootdx_next.bse import BseRegistry
from mootdx_next.bse import bse_registry as default_bse_registry
from mootdx_next.config_files import get_zhb_file
from mootdx_next.config_files import parse_ipo_subscriptions
from mootdx_next.config_files import parse_sp_blocks
from mootdx_next.config_files import parse_stock_statistics
from mootdx_next.config_files import parse_stock_statistics2
from mootdx_next.config_files import parse_tdx_block_aliases
from mootdx_next.config_files import parse_tdx_block_indexes
from mootdx_next.config_files import parse_tdx_industries
from mootdx_next.config_files import SP_BLOCK_FILENAME
from mootdx_next.config_files import TDX_BK_FILENAME
from mootdx_next.config_files import TDX_HY_FILENAME
from mootdx_next.config_files import TDX_STAT2_FILENAME
from mootdx_next.config_files import TDX_STAT_FILENAME
from mootdx_next.config_files import TDX_ZS_FILENAME
from mootdx_next.config_files import XGSG_FILENAME
from mootdx_next.config_files import ZHB_FILENAME
from mootdx_next.config_files import ZhbRegistry
from mootdx_next.config_files import zhb_registry
from mootdx_next.constants import HQ_HOSTS
from mootdx_next.constants import MAX_HISTORY_TRANSACTION_COUNT
from mootdx_next.constants import MAX_LIMIT_PRICE_COUNT
from mootdx_next.constants import MAX_TRANSACTION_COUNT
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import ProtocolDecodeError
from mootdx_next.errors import TransportError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.interfaces import AbstractScheduler
from mootdx_next.interfaces import AbstractTransport
from mootdx_next.limits import calculate_normal_stock_price_limit
from mootdx_next.limits import get_price_limit_snapshot
from mootdx_next.limits import refresh_price_limit_snapshot
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.minute_bars import rebuild_minute_bars_241
from mootdx_next.params import normalize_date
from mootdx_next.params import normalize_frequency
from mootdx_next.params import today_yyyymmdd
from mootdx_next.protocol import StdQuoteProtocol
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool
from mootdx_next.securities import classify_security
from mootdx_next.securities import Security
from mootdx_next.securities import SecurityRegistry
from mootdx_next.securities import security_registry as default_security_registry
from mootdx_next.symbols import get_stock_market
from mootdx_next.symbols import get_stock_markets
from mootdx_next.symbols import get_security_coefficient
from mootdx_next.symbols import get_security_type
from mootdx_next.symbols import normalize_symbol
from mootdx_next.symbols import normalize_symbol_input
from mootdx_next.transport.socket_transport import SyncSocketTransport
from mootdx_next.trading_calendar import TradingCalendarRegistry
from mootdx_next.trading_calendar import trading_calendar_registry as default_trading_calendar_registry

BLOCK_CHUNK_SIZE = 0x7530
DEFAULT_MAX_REPORT_FILE_SIZE = 256 * 1024 * 1024
TRANSACTION_MAX_OFFSET = MAX_TRANSACTION_COUNT
HISTORY_TRANSACTION_MAX_OFFSET = MAX_HISTORY_TRANSACTION_COUNT
LIMIT_PRICE_MAX_OFFSET = MAX_LIMIT_PRICE_COUNT
BAR_PAGE_SIZE = 800
BAR_MAX_START = 0xFFFF
F10_CONTENT_PAGE_SIZE = 0x7800
BarPredicate = Callable[[Mapping[str, object]], bool]
DIRECT_PRICE_TYPES = frozenset(
    {
        "SH_A_STOCK",
        "SH_B_STOCK",
        "SH_INDEX",
        "SH_FUND",
        "SZ_A_STOCK",
        "SZ_B_STOCK",
        "SZ_INDEX",
        "SZ_FUND",
        "BJ_STOCK",
    }
)

REQUEST_APIS = frozenset(
    {
        "stock_count",
        "stock_page",
        "stocks",
        "securities",
        "security",
        "stock_codes",
        "etf_codes",
        "index_codes",
        "quotes",
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
        "is_trading_day",
        "trading_days",
        "finance",
        "block_file_raw",
        "report_file",
        "zhb_files",
        "tdx_block_indexes",
        "tdx_block_aliases",
        "block_with_index",
        "sp_blocks",
        "tdx_industries",
        "ipo_subscriptions",
        "stock_statistics",
        "stock_statistics2",
        "block",
        "xdxr",
        "iter_xdxr",
        "equity_at",
        "turnover",
        "adjustment_factors",
        "f10_categories",
        "f10_content",
        "f10_content_range",
    }
)


def _default_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in HQ_HOSTS]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _date_range(start_date: str | int, end_date: str | int) -> Iterator[str]:
    start = datetime.strptime(normalize_date(start_date), "%Y%m%d").date()
    end = datetime.strptime(normalize_date(end_date), "%Y%m%d").date()
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    current = start
    while current <= end:
        yield current.strftime("%Y%m%d")
        current += timedelta(days=1)


def _symbol_iterable(symbols: Iterable[str] | str) -> tuple[str, ...]:
    values = (symbols,) if isinstance(symbols, str) else tuple(symbols)
    for symbol in values:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbols must contain non-blank strings")
    return values


def _canonical_security_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidSymbolError("symbol cannot be blank")
    market = int(get_stock_market(symbol, string=False))
    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        raise InvalidSymbolError("symbol must contain a six-digit numeric code")
    prefix = {0: "sz", 1: "sh", 2: "bj"}[market]
    return f"{prefix}{code}"


def _normalize_as_of_date(value: str | int | datetime | Date) -> Date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    return datetime.strptime(normalize_date(value), "%Y%m%d").date()


def _xdxr_date(row: Mapping[str, object]) -> Date:
    try:
        return Date(
            int(row["year"]),
            int(row["month"]),
            int(row["day"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolDecodeError("xdxr row has an invalid event date") from exc


class SyncClient:
    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
        config_registry: ZhbRegistry | None = None,
        bse_registry: BseRegistry | None = None,
        bse_provider: BseProvider | None = None,
        security_registry: SecurityRegistry | None = None,
        trading_calendar_registry: TradingCalendarRegistry | None = None,
    ) -> None:
        if bse_registry is not None and bse_provider is not None:
            raise ValueError("bse_registry and bse_provider are mutually exclusive")
        self.transport = transport
        self.protocol = protocol or StdQuoteProtocol()
        self.max_retries = max_retries
        self.config_registry = config_registry or zhb_registry
        self.bse_registry = (
            bse_registry
            if bse_registry is not None
            else BseRegistry(bse_provider) if bse_provider is not None else default_bse_registry
        )
        self.security_registry = security_registry or default_security_registry
        self.trading_calendar_registry = trading_calendar_registry or default_trading_calendar_registry
        self._adjustment_service: Any | None = None
        self._closed = False
        self.connection_pool = connection_pool or ConnectionPool(
            transport_factory=transport.__class__ if transport is not None else SyncSocketTransport
        )
        self.scheduler = scheduler or ServerPool(
            servers=servers or _default_servers(),
            connection_pool=self.connection_pool,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self.connection_pool.close_all()
        self._closed = True

    def reconnect(self) -> None:
        self.connection_pool.close_all()
        self._closed = False

    def request(self, api: str, **kwargs: Any) -> object:
        if api not in REQUEST_APIS:
            raise NotImplementedError(f"SyncClient.request() does not support api: {api}")
        return getattr(self, api)(**kwargs)

    def stock_count(self, market: int) -> int:
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError(f"unsupported market for stock_count: {market}")

        if market == 2:
            return len(self.bse_registry.get())

        context = RequestContext(api="stock_count", params={"market": market})
        payload = self.protocol.encode("stock_count", market=market)
        envelope = self._send(context, payload)
        return int(self.protocol.decode("stock_count", envelope))

    def stock_page(
        self,
        market: int,
        start: int = 0,
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError(f"unsupported market for stock_page: {market}")
        if start < 0 or start > 0xFFFF:
            raise ValueError("start must be between 0 and 65535")

        if market == 2:
            snapshot = self.bse_registry.get(refresh=bool(refresh))
            return [item.to_stock_dict() for item in snapshot[start : start + 1000]]

        context = RequestContext(api="stock_list_page", params={"market": market, "start": start})
        payload = self.protocol.encode("stock_list_page", market=market, start=start)
        envelope = self._send(context, payload)
        page = self.protocol.decode("stock_list_page", envelope)
        for row in page:
            row["market"] = market
            row["source"] = "tdx"
        return list(page)

    def stocks(self, market: int, refresh: bool = False) -> list[dict[str, object]]:
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError(f"unsupported market for stocks: {market}")

        if market == 2:
            return [item.to_stock_dict() for item in self.bse_registry.get(refresh=bool(refresh))]

        count = self.stock_count(market)
        if count <= 0:
            return []

        rows: list[dict[str, object]] = []
        for start in range(0, count, 1000):
            rows.extend(self.stock_page(market, start=start))

        return rows

    def securities(self, refresh: bool = False) -> list[dict[str, object]]:
        snapshot = self.security_registry.get(
            self._load_security_directory,
            refresh=bool(refresh),
        )
        return [item.to_dict() for item in snapshot]

    def security(self, symbol: str, refresh: bool = False) -> dict[str, object] | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        code = normalize_symbol(normalized_symbol)
        if len(code) != 6 or not code.isdigit():
            raise InvalidSymbolError("security requires a six-digit numeric symbol")
        snapshot = self.security_registry.get(
            self._load_security_directory,
            refresh=bool(refresh),
        )
        matched = next(
            (item for item in snapshot if item.market == market and item.code == code),
            None,
        )
        return None if matched is None else matched.to_dict()

    def stock_codes(self, refresh: bool = False) -> list[str]:
        return self._security_codes("stock", refresh=refresh)

    def etf_codes(self, refresh: bool = False) -> list[str]:
        return self._security_codes("etf", refresh=refresh)

    def index_codes(self, refresh: bool = False) -> list[str]:
        return self._security_codes("index", refresh=refresh)

    def quotes(self, symbol: str | list[str] | None = None) -> list[dict[str, object]]:
        normalized = normalize_symbol_input(symbol)
        if not normalized:
            return []

        symbols = get_stock_markets(normalized)
        context = RequestContext(api="quotes", params={"symbol": normalized})
        payload = self.protocol.encode("quotes", symbols=symbols)
        envelope = self._send(context, payload)
        price_coefficients = {
            (int(market), str(code)): self._price_coefficient(int(market), str(code))
            for market, code in symbols
        }
        return list(
            self.protocol.decode(
                "quotes",
                envelope,
                price_coefficients=price_coefficients,
            )
        )

    def limit_prices(
        self,
        start: int = 0,
        count: int = LIMIT_PRICE_MAX_OFFSET,
    ) -> list[dict[str, object]]:
        if start < 0 or start > 0xFFFF:
            raise ValueError("start must be between 0 and 65535")
        if count <= 0 or count > LIMIT_PRICE_MAX_OFFSET:
            raise ValueError(f"count must be between 1 and {LIMIT_PRICE_MAX_OFFSET}")

        context = RequestContext(api="limit_prices", params={"start": start, "count": count})
        payload = self.protocol.encode("limit_prices", start=start, count=count)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("limit_prices", envelope))

    def price_limit(self, symbol: str, refresh: bool = False) -> dict[str, object] | None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        code = normalize_symbol(normalized_symbol)
        if len(code) != 6 or not code.isdigit():
            raise InvalidSymbolError("price_limit requires a six-digit numeric symbol")

        loader = self._all_limit_prices
        snapshot = refresh_price_limit_snapshot(loader) if refresh else get_price_limit_snapshot(loader)
        matched = next((item for item in snapshot if item.market == market and item.code == code), None)
        if matched is not None:
            return matched.to_dict(source="server")

        quotes = self.quotes(normalized_symbol)
        quote = next(
            (
                item
                for item in quotes
                if int(item.get("market", -1)) == market and str(item.get("code", "")) == code
            ),
            None,
        )
        if quote is None:
            return None

        calculated = calculate_normal_stock_price_limit(market, code, float(quote.get("last_close", 0)))
        if calculated is None:
            return None
        return calculated.to_dict(source="calculated")

    def bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if start < 0 or start > BAR_MAX_START:
            raise ValueError(f"start must be between 0 and {BAR_MAX_START}")
        if offset <= 0 or offset > 800:
            raise ValueError("offset must be between 1 and 800")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        code = normalize_symbol(normalized_symbol)
        normalized_frequency = normalize_frequency(frequency)

        context = RequestContext(
            api="bars",
            params={
                "symbol": normalized_symbol,
                "market": market,
                "frequency": normalized_frequency,
                "start": start,
                "offset": offset,
            },
        )
        payload = self.protocol.encode(
            "bars",
            frequency=normalized_frequency,
            market=market,
            code=code,
            start=start,
            count=offset,
        )
        envelope = self._send(context, payload)
        return list(self.protocol.decode("bars", envelope, frequency=normalized_frequency))

    def bars_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        frequency: int | str = 9,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        normalized_frequency = normalize_frequency(frequency)
        return self._bars_until(
            lambda start, count: self.bars(
                symbol,
                frequency=normalized_frequency,
                start=start,
                offset=count,
            ),
            predicate,
            page_size=page_size,
            max_pages=max_pages,
        )

    def bars_all(
        self,
        symbol: str,
        frequency: int | str = 9,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return self.bars_until(
            symbol,
            lambda _row: False,
            frequency=frequency,
            page_size=page_size,
            max_pages=max_pages,
        )

    def minute_bars_241(
        self,
        symbol: str,
        start: int = 0,
        offset: int = BAR_PAGE_SIZE,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        rows = self.bars(symbol, frequency=8, start=start, offset=offset)
        return self._rebuild_minute_bars_241(
            symbol,
            rows,
            transaction_max_pages=transaction_max_pages,
        )

    def minute_bars_241_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        rows = self.bars_until(
            symbol,
            predicate,
            frequency=8,
            page_size=page_size,
            max_pages=max_pages,
        )
        return self._rebuild_minute_bars_241(
            symbol,
            rows,
            transaction_max_pages=transaction_max_pages,
        )

    def minute_bars_241_all(
        self,
        symbol: str,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return self.minute_bars_241_until(
            symbol,
            lambda _row: False,
            page_size=page_size,
            max_pages=max_pages,
            transaction_max_pages=transaction_max_pages,
        )

    def index_bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
        market: int | None = None,
    ) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if start < 0 or start > BAR_MAX_START:
            raise ValueError(f"start must be between 0 and {BAR_MAX_START}")
        if offset <= 0 or offset > 800:
            raise ValueError("offset must be between 1 and 800")

        normalized_symbol = symbol.strip()
        normalized_frequency = normalize_frequency(frequency)
        normalized_market = _get_index_market(normalized_symbol, market)
        code = normalize_symbol(normalized_symbol)

        context = RequestContext(
            api="index_bars",
            params={
                "symbol": normalized_symbol,
                "market": normalized_market,
                "frequency": normalized_frequency,
                "start": start,
                "offset": offset,
            },
        )
        payload = self.protocol.encode(
            "index_bars",
            frequency=normalized_frequency,
            market=normalized_market,
            code=code,
            start=start,
            count=offset,
        )
        envelope = self._send(context, payload)
        return list(self.protocol.decode("index_bars", envelope, frequency=normalized_frequency))

    def index_bars_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        frequency: int | str = 9,
        market: int | None = None,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        normalized_frequency = normalize_frequency(frequency)
        return self._bars_until(
            lambda start, count: self.index_bars(
                symbol,
                frequency=normalized_frequency,
                start=start,
                offset=count,
                market=market,
            ),
            predicate,
            page_size=page_size,
            max_pages=max_pages,
        )

    def index_bars_all(
        self,
        symbol: str,
        frequency: int | str = 9,
        market: int | None = None,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return self.index_bars_until(
            symbol,
            lambda _row: False,
            frequency=frequency,
            market=market,
            page_size=page_size,
            max_pages=max_pages,
        )

    def minutes(self, symbol: str, date: str | int) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for minutes: only sh/sz/bj are supported")

        normalized_date = normalize_date(date)
        code = normalize_symbol(normalized_symbol)
        price_coefficient = self._price_coefficient(market, code)
        context = RequestContext(
            api="minutes",
            params={"symbol": normalized_symbol, "market": market, "date": normalized_date},
        )
        payload = self.protocol.encode("minutes", market=market, code=code, date=normalized_date)
        envelope = self._send(context, payload)
        return list(
            self.protocol.decode(
                "minutes",
                envelope,
                market=market,
                code=code,
                date=normalized_date,
                price_coefficient=price_coefficient,
            )
        )

    def minute(self, symbol: str) -> list[dict[str, object]]:
        return self.minutes(symbol=symbol, date=today_yyyymmdd())

    def call_auction(self, symbol: str) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for call_auction: only sh/sz/bj are supported")
        code = normalize_symbol(normalized_symbol)
        if len(code) != 6 or not code.isdigit():
            raise InvalidSymbolError("call_auction requires a six-digit numeric symbol")

        context = RequestContext(
            api="call_auction",
            params={"symbol": normalized_symbol, "market": market},
        )
        payload = self.protocol.encode("call_auction", market=market, code=code)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("call_auction", envelope))

    def transaction(
        self,
        symbol: str,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if start < 0:
            raise ValueError("start must be >= 0")
        if offset <= 0 or offset > TRANSACTION_MAX_OFFSET:
            raise ValueError(f"offset must be between 1 and {TRANSACTION_MAX_OFFSET}")
        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for transaction: only sh/sz/bj are supported")

        code = normalize_symbol(normalized_symbol)
        price_coefficient = self._price_coefficient(market, code)
        context = RequestContext(
            api="transaction",
            params={"symbol": normalized_symbol, "market": market, "start": start, "offset": offset},
        )
        payload = self.protocol.encode("transaction", market=market, code=code, start=start, count=offset)
        envelope = self._send(context, payload)
        return list(
            self.protocol.decode(
                "transaction",
                envelope,
                market=market,
                code=code,
                price_coefficient=price_coefficient,
            )
        )

    def transaction_all(
        self,
        symbol: str,
        page_size: int = TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return self._collect_transaction_pages(
            lambda start, count: self.transaction(symbol, start=start, offset=count),
            page_size=page_size,
            page_max=TRANSACTION_MAX_OFFSET,
            max_pages=max_pages,
        )

    def transactions(
        self,
        symbol: str,
        date: str | int,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if start < 0:
            raise ValueError("start must be >= 0")
        if offset <= 0 or offset > HISTORY_TRANSACTION_MAX_OFFSET:
            raise ValueError(f"offset must be between 1 and {HISTORY_TRANSACTION_MAX_OFFSET}")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for transactions: only sh/sz/bj are supported")

        normalized_date = normalize_date(date)
        code = normalize_symbol(normalized_symbol)
        price_coefficient = self._price_coefficient(market, code)
        context = RequestContext(
            api="transactions",
            params={
                "symbol": normalized_symbol,
                "market": market,
                "date": normalized_date,
                "start": start,
                "offset": offset,
            },
        )
        payload = self.protocol.encode(
            "transactions",
            market=market,
            code=code,
            start=start,
            count=offset,
            date=normalized_date,
        )
        envelope = self._send(context, payload)
        return list(
            self.protocol.decode(
                "transactions",
                envelope,
                market=market,
                code=code,
                date=normalized_date,
                price_coefficient=price_coefficient,
            )
        )

    def transactions_day(
        self,
        symbol: str,
        date: str | int,
        page_size: int = HISTORY_TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        normalized_date = normalize_date(date)
        return self._collect_transaction_pages(
            lambda start, count: self.transactions(
                symbol,
                normalized_date,
                start=start,
                offset=count,
            ),
            page_size=page_size,
            page_max=HISTORY_TRANSACTION_MAX_OFFSET,
            max_pages=max_pages,
        )

    def iter_transactions(
        self,
        symbol: str,
        start_date: str | int,
        end_date: str | int,
        *,
        include_empty: bool = False,
        trading_days_only: bool = True,
        refresh_calendar: bool = False,
        page_size: int = HISTORY_TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> Iterator[tuple[str, list[dict[str, object]]]]:
        dates = (
            self.trading_days(start_date, end_date, refresh=refresh_calendar)
            if trading_days_only
            else tuple(_date_range(start_date, end_date))
        )
        for date in dates:
            rows = self.transactions_day(
                symbol,
                date,
                page_size=page_size,
                max_pages=max_pages,
            )
            if rows or include_empty:
                yield date, rows

    def trading_days(
        self,
        start_date: str | int | None = None,
        end_date: str | int | None = None,
        *,
        refresh: bool = False,
    ) -> tuple[str, ...]:
        snapshot = self.trading_calendar_registry.get(
            self._load_trading_calendar,
            refresh=bool(refresh),
        )
        if start_date is None and end_date is None:
            return snapshot
        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date must be provided together")
        start = normalize_date(start_date)
        end = normalize_date(end_date)
        if end < start:
            raise ValueError("end_date must be on or after start_date")
        return self.trading_calendar_registry.between(start, end)

    def is_trading_day(self, date: str | int, *, refresh: bool = False) -> bool:
        normalized = normalize_date(date)
        self.trading_calendar_registry.get(
            self._load_trading_calendar,
            refresh=bool(refresh),
        )
        return self.trading_calendar_registry.contains(normalized)

    def finance(self, symbol: str) -> dict[str, object]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for finance: only sh/sz/bj are supported")

        code = normalize_symbol(normalized_symbol)
        context = RequestContext(api="finance", params={"symbol": normalized_symbol, "market": market})
        payload = self.protocol.encode("finance", market=market, code=code)
        envelope = self._send(context, payload)
        return dict(self.protocol.decode("finance", envelope))

    def block_file_raw(self, block_file: str) -> bytes:
        block_file = _validate_remote_filename(block_file)
        context = RequestContext(api="block_info_meta", params={"block_file": block_file})
        meta_payload = self.protocol.encode("block_info_meta", block_file=block_file)
        meta_envelope = self._send(context, meta_payload)
        meta = self.protocol.decode("block_info_meta", meta_envelope)
        size = int(meta["size"])

        if size <= 0:
            return b""

        content = bytearray()
        for start in range(0, size, BLOCK_CHUNK_SIZE):
            chunk_size = min(BLOCK_CHUNK_SIZE, size - start)
            piece_context = RequestContext(
                api="block_info",
                params={"block_file": block_file, "start": start, "size": chunk_size},
            )
            payload = self.protocol.encode(
                "block_info",
                block_file=block_file,
                start=start,
                size=chunk_size,
            )
            envelope = self._send(piece_context, payload)
            piece = bytes(self.protocol.decode("block_info", envelope))
            if len(piece) != chunk_size:
                raise ProtocolDecodeError(
                    f"block file {block_file} truncated at offset {start}: "
                    f"expected {chunk_size} bytes, got {len(piece)}"
                )
            content.extend(piece)

        return bytes(content)

    def report_file(
        self,
        filename: str,
        max_bytes: int = DEFAULT_MAX_REPORT_FILE_SIZE,
    ) -> bytes:
        filename = _validate_remote_filename(filename)
        if max_bytes <= 0:
            raise ValueError("max_bytes must be greater than zero")

        content = bytearray()
        for start in range(0, max_bytes, BLOCK_CHUNK_SIZE):
            chunk_size = min(BLOCK_CHUNK_SIZE, max_bytes - start)
            context = RequestContext(
                api="report_file",
                params={"filename": filename, "start": start, "size": chunk_size},
            )
            payload = self.protocol.encode(
                "block_info",
                block_file=filename,
                start=start,
                size=chunk_size,
            )
            envelope = self._send(context, payload)
            piece = bytes(self.protocol.decode("block_info", envelope))
            content.extend(piece)
            if len(piece) < chunk_size:
                return bytes(content)

        raise ProtocolDecodeError(f"report file {filename} exceeds max_bytes={max_bytes}")

    def zhb_files(self, refresh: bool = False) -> Mapping[str, bytes]:
        snapshot = self.config_registry.get(
            lambda: self.report_file(ZHB_FILENAME),
            refresh=bool(refresh),
        )
        return snapshot.files

    def tdx_block_indexes(self, refresh: bool = False) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        return parse_tdx_block_indexes(get_zhb_file(files, TDX_ZS_FILENAME))

    def tdx_block_aliases(self, refresh: bool = False) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        return parse_tdx_block_aliases(get_zhb_file(files, TDX_BK_FILENAME))

    def block_with_index(
        self,
        block_file: str = "block_gn.dat",
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        rows = self.block(block_file)
        indexes = self.tdx_block_indexes(refresh=refresh)
        aliases = self.tdx_block_aliases(refresh=False)
        name_to_index = {str(item["name"]): str(item["code"]) for item in indexes}
        short_to_full = {str(item["short_name"]): str(item["full_name"]) for item in aliases}

        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            name = str(item.get("blockname", ""))
            full_name = short_to_full.get(name, name)
            item["block_index"] = name_to_index.get(name, name_to_index.get(full_name, ""))
            result.append(item)
        return result

    def sp_blocks(
        self,
        name: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        rows = parse_sp_blocks(get_zhb_file(files, SP_BLOCK_FILENAME))
        if name is None:
            return rows
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return [row for row in rows if row["blockname"] == normalized]

    def tdx_industries(self) -> list[dict[str, object]]:
        return parse_tdx_industries(self.block_file_raw(TDX_HY_FILENAME))

    def ipo_subscriptions(self, refresh: bool = False) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        return parse_ipo_subscriptions(get_zhb_file(files, XGSG_FILENAME))

    def stock_statistics(self, refresh: bool = False) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        return parse_stock_statistics(get_zhb_file(files, TDX_STAT_FILENAME))

    def stock_statistics2(self, refresh: bool = False) -> list[dict[str, object]]:
        files = self.zhb_files(refresh=refresh)
        return parse_stock_statistics2(get_zhb_file(files, TDX_STAT2_FILENAME))

    def block(self, block_file: str = "block.dat") -> list[dict[str, object]]:
        content = self.block_file_raw(block_file)
        if not content:
            return []

        return _parse_block_content(content)

    def xdxr(self, symbol: str) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for xdxr: only sh/sz/bj are supported")

        code = normalize_symbol(normalized_symbol)
        context = RequestContext(api="xdxr", params={"symbol": normalized_symbol, "market": market})
        payload = self.protocol.encode("xdxr", market=market, code=code)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("xdxr", envelope))

    def iter_xdxr(
        self,
        symbols: Iterable[str] | str | None = None,
        *,
        refresh: bool = False,
        retries: int = 1,
    ) -> Iterator[tuple[str, list[dict[str, object]]]]:
        if retries < 0:
            raise ValueError("retries must be >= 0")
        selected = self.stock_codes(refresh=refresh) if symbols is None else _symbol_iterable(symbols)
        for symbol in selected:
            canonical = _canonical_security_symbol(symbol)
            for attempt in range(retries + 1):
                try:
                    rows = self.xdxr(canonical)
                    break
                except TransportError:
                    if attempt == retries:
                        raise
            yield canonical, rows

    def equity_at(
        self,
        symbol: str,
        as_of: str | int | datetime | Date,
    ) -> dict[str, object] | None:
        target = _normalize_as_of_date(as_of)
        candidates: list[tuple[Date, dict[str, object]]] = []
        for row in self.xdxr(symbol):
            if _optional_int(row.get("category")) not in {2, 3, 5, 7, 8, 9, 10}:
                continue
            event_date = _xdxr_date(row)
            if event_date <= target:
                candidates.append((event_date, row))
        if not candidates:
            return None

        event_date, row = max(candidates, key=lambda item: item[0])
        float_shares = _optional_float(row.get("panhouliutong_shares"))
        total_shares = _optional_float(row.get("houzongguben_shares"))
        return {
            "market": _optional_int(row.get("market")),
            "code": str(row.get("code", normalize_symbol(symbol))),
            "symbol": str(row.get("symbol", _canonical_security_symbol(symbol))),
            "date": event_date.strftime("%Y-%m-%d"),
            "datetime": row.get("datetime"),
            "category": _optional_int(row.get("category")),
            "name": row.get("name"),
            "float_shares": float_shares,
            "total_shares": total_shares,
        }

    def turnover(
        self,
        symbol: str,
        as_of: str | int | datetime | Date,
        volume: int | float,
        *,
        volume_unit: str = "shares",
    ) -> float | None:
        if isinstance(volume, bool):
            raise ValueError("volume must be a non-negative finite number")
        normalized_volume = float(volume)
        if normalized_volume < 0 or not math.isfinite(normalized_volume):
            raise ValueError("volume must be a non-negative finite number")
        normalized_unit = str(volume_unit).strip().lower()
        if normalized_unit not in {"shares", "lots"}:
            raise ValueError("volume_unit must be 'shares' or 'lots'")
        if normalized_unit == "lots":
            normalized_volume *= 100

        equity = self.equity_at(symbol, as_of)
        if equity is None:
            return None
        float_shares = _optional_float(equity.get("float_shares"))
        if float_shares is None or float_shares <= 0:
            return None
        return normalized_volume / float_shares * 100

    def adjustment_factors(self, symbol: str) -> list[dict[str, object]]:
        if self._adjustment_service is None:
            from mootdx_next.adjustments import AdjustmentService

            self._adjustment_service = AdjustmentService(self)
        frame = self._adjustment_service.factors(symbol).reset_index()
        rows = frame.to_dict("records")
        for row in rows:
            timestamp = row.get("datetime")
            if hasattr(timestamp, "strftime"):
                row["datetime"] = timestamp.strftime("%Y-%m-%d %H:%M")
                row["date"] = timestamp.strftime("%Y-%m-%d")
            for key, value in tuple(row.items()):
                if isinstance(value, float) and math.isnan(value):
                    row[key] = None
        return rows

    def f10_categories(self, symbol: str) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for f10: only sh/sz/bj are supported")

        code = normalize_symbol(normalized_symbol)
        context = RequestContext(api="f10_categories", params={"symbol": normalized_symbol, "market": market})
        payload = self.protocol.encode("f10_categories", market=market, code=code)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("f10_categories", envelope))

    def f10_content(self, symbol: str, name: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if not isinstance(name, str) or not name.strip():
            raise UnknownF10CategoryError("f10 category name cannot be blank")

        normalized_symbol = symbol.strip()
        normalized_name = name.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for f10: only sh/sz/bj are supported")

        categories = self.f10_categories(normalized_symbol)
        matched = next((item for item in categories if item["name"] == normalized_name), None)
        if matched is None:
            raise UnknownF10CategoryError(f"unknown f10 category: {normalized_name}")

        expected_length = int(matched["length"])
        content = self.f10_content_range(
            normalized_symbol,
            str(matched["filename"]),
            int(matched["start"]),
            expected_length,
        )
        if len(content) != expected_length:
            raise ProtocolDecodeError(
                f"f10 category {normalized_name!r} truncated: "
                f"expected {expected_length} bytes, got {len(content)}"
            )
        return content.decode("gbk", "ignore")

    def f10_content_range(
        self,
        symbol: str,
        filename: str,
        start: int,
        length: int,
    ) -> bytes:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("f10 filename cannot be blank")
        if start < 0 or start > 0xFFFFFFFF:
            raise ValueError("start must be between 0 and 4294967295")
        if length < 0 or length > 0xFFFFFFFF - start:
            raise ValueError("length exceeds the 32-bit f10 range")
        if length == 0:
            return b""

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for f10: only sh/sz/bj are supported")
        code = normalize_symbol(normalized_symbol)

        chunks: list[bytes] = []
        received = 0
        while received < length:
            page_length = min(F10_CONTENT_PAGE_SIZE, length - received)
            page_start = start + received
            context = RequestContext(
                api="f10_content",
                params={
                    "symbol": normalized_symbol,
                    "market": market,
                    "filename": filename,
                    "start": page_start,
                    "length": page_length,
                },
            )
            payload = self.protocol.encode(
                "f10_content",
                market=market,
                code=code,
                filename=filename,
                start=page_start,
                length=page_length,
            )
            envelope = self._send(context, payload)
            decoded = self.protocol.decode("f10_content", envelope, raw=True)
            chunk = decoded.encode("gbk") if isinstance(decoded, str) else bytes(decoded)
            if len(chunk) > page_length:
                raise ProtocolDecodeError(
                    f"f10 range returned {len(chunk)} bytes for a {page_length}-byte request"
                )
            chunks.append(chunk)
            received += len(chunk)
            if len(chunk) < page_length:
                break
        return b"".join(chunks)

    def _all_limit_prices(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for start in range(0, 0x10000, LIMIT_PRICE_MAX_OFFSET):
            count = min(LIMIT_PRICE_MAX_OFFSET, 0x10000 - start)
            page = self.limit_prices(start=start, count=count)
            rows.extend(page)
            if len(page) < count:
                break
        return rows

    def _security_codes(self, security_type: str, *, refresh: bool) -> list[str]:
        snapshot = self.security_registry.get(
            self._load_security_directory,
            refresh=bool(refresh),
        )
        return [item.symbol for item in snapshot if item.security_type == security_type]

    def _rebuild_minute_bars_241(
        self,
        symbol: str,
        rows: list[dict[str, object]],
        *,
        transaction_max_pages: int | None,
    ) -> list[dict[str, object]]:
        dates = {
            str(row.get("datetime", ""))[:10].replace("-", "")
            for row in rows
            if str(row.get("datetime", ""))[11:16] == "09:31"
        }
        transactions_by_date: dict[str, list[dict[str, object]]] = {}
        today = today_yyyymmdd()
        for date in sorted(dates):
            if date == today:
                transactions_by_date[date] = self.transaction_all(
                    symbol,
                    page_size=TRANSACTION_MAX_OFFSET,
                    max_pages=transaction_max_pages,
                )
            else:
                transactions_by_date[date] = self.transactions_day(
                    symbol,
                    date,
                    page_size=HISTORY_TRANSACTION_MAX_OFFSET,
                    max_pages=transaction_max_pages,
                )
        return rebuild_minute_bars_241(rows, transactions_by_date)

    @staticmethod
    def _collect_transaction_pages(
        fetch: Callable[[int, int], list[dict[str, object]]],
        *,
        page_size: int,
        page_max: int,
        max_pages: int | None,
    ) -> list[dict[str, object]]:
        if page_size <= 0 or page_size > page_max:
            raise ValueError(f"page_size must be between 1 and {page_max}")
        available_pages = BAR_MAX_START // page_size + 1
        if max_pages is None:
            page_limit = available_pages
        else:
            if max_pages <= 0:
                raise ValueError("max_pages must be greater than zero")
            page_limit = min(int(max_pages), available_pages)

        combined: list[dict[str, object]] = []
        previous_page: list[dict[str, object]] | None = None
        for page_index in range(page_limit):
            page = [dict(row) for row in fetch(page_index * page_size, page_size)]
            if not page:
                break
            if page == previous_page:
                raise ProtocolDecodeError("transaction pagination did not advance")
            combined = page + combined
            if len(page) < page_size:
                break
            previous_page = page
        return combined

    @staticmethod
    def _bars_until(
        fetch: Callable[[int, int], list[dict[str, object]]],
        predicate: BarPredicate,
        *,
        page_size: int,
        max_pages: int | None,
    ) -> list[dict[str, object]]:
        if not callable(predicate):
            raise TypeError("predicate must be callable")
        if page_size <= 0 or page_size > BAR_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {BAR_PAGE_SIZE}")
        available_pages = BAR_MAX_START // page_size + 1
        if max_pages is None:
            page_limit = available_pages
        else:
            if max_pages <= 0:
                raise ValueError("max_pages must be greater than zero")
            page_limit = min(int(max_pages), available_pages)

        combined: list[dict[str, object]] = []
        previous_signature: tuple[object, object, int] | None = None
        for page_index in range(page_limit):
            start = page_index * page_size
            page = [dict(row) for row in fetch(start, page_size)]
            if not page:
                break
            signature = (page[0].get("datetime"), page[-1].get("datetime"), len(page))
            if signature == previous_signature:
                raise ProtocolDecodeError("bar pagination did not advance")
            previous_signature = signature

            matched_index: int | None = None
            for index in range(len(page) - 1, -1, -1):
                if predicate(page[index]):
                    matched_index = index
                    break
            if matched_index is not None:
                combined = page[matched_index:] + combined
                break

            combined = page + combined
            if len(page) < page_size:
                break

        for index in range(1, len(combined)):
            combined[index]["previous_close"] = combined[index - 1].get("close")
        return combined

    def _load_security_directory(self) -> tuple[Security, ...]:
        securities: list[Security] = []
        for market in (1, 0, 2):
            for row in self.stocks(market):
                code = str(row.get("code", ""))
                securities.append(
                    Security(
                        market=market,
                        code=code,
                        name=str(row.get("name", "")),
                        security_type=classify_security(market, code),
                        volunit=_optional_int(row.get("volunit")),
                        decimal_point=_optional_int(row.get("decimal_point")),
                        pre_close=_optional_float(row.get("pre_close")),
                        source=str(row.get("source", "tdx")),
                    )
                )
        if not any(item.market == 2 and item.code == "899050" for item in securities):
            securities.insert(
                0,
                Security(
                    market=2,
                    code="899050",
                    name="北证50",
                    security_type="index",
                    volunit=100,
                    decimal_point=2,
                    source="synthetic",
                ),
            )
        return tuple(securities)

    def _load_trading_calendar(self) -> tuple[str, ...]:
        rows = self.index_bars_all("sh000001", frequency=9)
        days: list[str] = []
        for row in rows:
            value = str(row.get("datetime", ""))[:10].replace("-", "")
            if len(value) != 8 or not value.isdigit():
                raise ProtocolDecodeError("index bar has an invalid trading-calendar date")
            days.append(value)
        return tuple(days)

    def _price_coefficient(self, market: int, code: str) -> float:
        security = self.security_registry.find(market, code)
        if security is None and get_security_type(market, code) not in DIRECT_PRICE_TYPES:
            self.security_registry.get(self._load_security_directory)
            security = self.security_registry.find(market, code)

        if security is not None and security.decimal_point is not None:
            decimal_point = int(security.decimal_point)
            if 0 <= decimal_point <= 10:
                return 10.0 ** -decimal_point
        return get_security_coefficient(market, code)

    def _send(self, context: RequestContext, payload: bytes):
        if self._closed:
            self._closed = False

        excluded: set[tuple[str, int]] = set()
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            server = self.scheduler.select_server(context, excluded=excluded)
            server_key = (server.host, server.port)

            try:
                lease = self.connection_pool.acquire(server)
            except PoolExhaustedError as exc:
                self.scheduler.record_failure(server, exc)
                excluded.add(server_key)
                if attempt < self.max_retries:
                    continue
                raise

            try:
                envelope = lease.transport.send(context, payload, server)
                self.scheduler.record_success(server, lease.transport.metrics)
                self.connection_pool.release(lease)
                return envelope
            except Exception as exc:
                self.scheduler.record_failure(server, exc)
                self.connection_pool.discard(lease)
                excluded.add(server_key)
                if isinstance(exc, TransportError) and attempt < self.max_retries:
                    lease.transport.metrics.retry_count += 1
                    continue
                raise

        raise RuntimeError("unreachable")


class AsyncClient:
    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
        sync_client: SyncClient | None = None,
        config_registry: ZhbRegistry | None = None,
        bse_registry: BseRegistry | None = None,
        bse_provider: BseProvider | None = None,
        security_registry: SecurityRegistry | None = None,
        trading_calendar_registry: TradingCalendarRegistry | None = None,
    ) -> None:
        self._explicit_sync_client = sync_client
        self._thread_local = threading.local()
        self._clients_lock = threading.Lock()
        self._worker_clients: list[SyncClient] = []
        self._closed = False
        self._sync_client_kwargs = {
            "transport": transport,
            "protocol": protocol,
            "scheduler": scheduler,
            "connection_pool": connection_pool,
            "servers": servers,
            "max_retries": max_retries,
            "config_registry": config_registry,
            "bse_registry": bse_registry,
            "bse_provider": bse_provider,
            "security_registry": security_registry,
            "trading_calendar_registry": trading_calendar_registry,
        }

    @property
    def closed(self) -> bool:
        if self._explicit_sync_client is not None:
            return self._explicit_sync_client.closed
        return self._closed

    def close(self) -> None:
        if self._explicit_sync_client is not None:
            self._explicit_sync_client.close()
            return
        with self._clients_lock:
            clients = self._worker_clients
            self._worker_clients = []
            self._closed = True
        for client in clients:
            client.close()

    def reconnect(self) -> None:
        if self._explicit_sync_client is not None:
            self._explicit_sync_client.reconnect()
            return
        with self._clients_lock:
            clients = self._worker_clients
            self._worker_clients = []
            self._closed = False
        for client in clients:
            client.close()

    async def request(self, api: str, **kwargs: Any) -> object:
        return await asyncio.to_thread(self._call_sync, "request", api, **kwargs)

    async def stock_count(self, market: int) -> int:
        return int(await asyncio.to_thread(self._call_sync, "stock_count", market))

    async def stock_page(
        self,
        market: int,
        start: int = 0,
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "stock_page",
                market,
                start,
                refresh,
            )
        )

    async def stocks(self, market: int, refresh: bool = False) -> list[dict[str, object]]:
        if refresh:
            return list(await asyncio.to_thread(self._call_sync, "stocks", market, refresh=True))
        return list(await asyncio.to_thread(self._call_sync, "stocks", market))

    async def securities(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "securities", refresh))

    async def security(self, symbol: str, refresh: bool = False) -> dict[str, object] | None:
        result = await asyncio.to_thread(self._call_sync, "security", symbol, refresh)
        return None if result is None else dict(result)

    async def stock_codes(self, refresh: bool = False) -> list[str]:
        return list(await asyncio.to_thread(self._call_sync, "stock_codes", refresh))

    async def etf_codes(self, refresh: bool = False) -> list[str]:
        return list(await asyncio.to_thread(self._call_sync, "etf_codes", refresh))

    async def index_codes(self, refresh: bool = False) -> list[str]:
        return list(await asyncio.to_thread(self._call_sync, "index_codes", refresh))

    async def quotes(self, symbol: str | list[str] | None = None) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "quotes", symbol))

    async def limit_prices(
        self,
        start: int = 0,
        count: int = LIMIT_PRICE_MAX_OFFSET,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "limit_prices", start, count))

    async def price_limit(self, symbol: str, refresh: bool = False) -> dict[str, object] | None:
        result = await asyncio.to_thread(self._call_sync, "price_limit", symbol, refresh)
        return None if result is None else dict(result)

    async def bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "bars", symbol, frequency, start, offset))

    async def bars_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        frequency: int | str = 9,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "bars_until",
                symbol,
                predicate,
                frequency,
                page_size,
                max_pages,
            )
        )

    async def bars_all(
        self,
        symbol: str,
        frequency: int | str = 9,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "bars_all",
                symbol,
                frequency,
                page_size,
                max_pages,
            )
        )

    async def minute_bars_241(
        self,
        symbol: str,
        start: int = 0,
        offset: int = BAR_PAGE_SIZE,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "minute_bars_241",
                symbol,
                start,
                offset,
                transaction_max_pages=transaction_max_pages,
            )
        )

    async def minute_bars_241_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "minute_bars_241_until",
                symbol,
                predicate,
                page_size,
                max_pages,
                transaction_max_pages=transaction_max_pages,
            )
        )

    async def minute_bars_241_all(
        self,
        symbol: str,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
        *,
        transaction_max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "minute_bars_241_all",
                symbol,
                page_size,
                max_pages,
                transaction_max_pages=transaction_max_pages,
            )
        )

    async def minutes(self, symbol: str, date: str | int) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "minutes", symbol, date))

    async def minute(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "minute", symbol))

    async def call_auction(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "call_auction", symbol))

    async def transaction(self, symbol: str, start: int = 0, offset: int = 800) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "transaction", symbol, start, offset))

    async def transaction_all(
        self,
        symbol: str,
        page_size: int = TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "transaction_all",
                symbol,
                page_size,
                max_pages,
            )
        )

    async def transactions(
        self,
        symbol: str,
        date: str | int,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "transactions", symbol, date, start, offset))

    async def transactions_day(
        self,
        symbol: str,
        date: str | int,
        page_size: int = HISTORY_TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "transactions_day",
                symbol,
                date,
                page_size,
                max_pages,
            )
        )

    async def iter_transactions(
        self,
        symbol: str,
        start_date: str | int,
        end_date: str | int,
        *,
        include_empty: bool = False,
        trading_days_only: bool = True,
        refresh_calendar: bool = False,
        page_size: int = HISTORY_TRANSACTION_MAX_OFFSET,
        max_pages: int | None = None,
    ) -> AsyncIterator[tuple[str, list[dict[str, object]]]]:
        dates = (
            await self.trading_days(start_date, end_date, refresh=refresh_calendar)
            if trading_days_only
            else tuple(_date_range(start_date, end_date))
        )
        for date in dates:
            rows = await self.transactions_day(
                symbol,
                date,
                page_size=page_size,
                max_pages=max_pages,
            )
            if rows or include_empty:
                yield date, rows

    async def trading_days(
        self,
        start_date: str | int | None = None,
        end_date: str | int | None = None,
        *,
        refresh: bool = False,
    ) -> tuple[str, ...]:
        result = await asyncio.to_thread(
            self._call_sync,
            "trading_days",
            start_date,
            end_date,
            refresh=refresh,
        )
        return tuple(result)

    async def is_trading_day(self, date: str | int, *, refresh: bool = False) -> bool:
        return bool(
            await asyncio.to_thread(
                self._call_sync,
                "is_trading_day",
                date,
                refresh=refresh,
            )
        )

    async def finance(self, symbol: str) -> dict[str, object]:
        return dict(await asyncio.to_thread(self._call_sync, "finance", symbol))

    async def xdxr(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "xdxr", symbol))

    async def iter_xdxr(
        self,
        symbols: Iterable[str] | str | None = None,
        *,
        refresh: bool = False,
        retries: int = 1,
    ) -> AsyncIterator[tuple[str, list[dict[str, object]]]]:
        selected = await self.stock_codes(refresh=refresh) if symbols is None else _symbol_iterable(symbols)
        if retries < 0:
            raise ValueError("retries must be >= 0")
        for symbol in selected:
            canonical = _canonical_security_symbol(symbol)
            for attempt in range(retries + 1):
                try:
                    rows = await self.xdxr(canonical)
                    break
                except TransportError:
                    if attempt == retries:
                        raise
            yield canonical, rows

    async def equity_at(
        self,
        symbol: str,
        as_of: str | int | datetime | Date,
    ) -> dict[str, object] | None:
        result = await asyncio.to_thread(self._call_sync, "equity_at", symbol, as_of)
        return None if result is None else dict(result)

    async def turnover(
        self,
        symbol: str,
        as_of: str | int | datetime | Date,
        volume: int | float,
        *,
        volume_unit: str = "shares",
    ) -> float | None:
        result = await asyncio.to_thread(
            self._call_sync,
            "turnover",
            symbol,
            as_of,
            volume,
            volume_unit=volume_unit,
        )
        return None if result is None else float(result)

    async def adjustment_factors(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "adjustment_factors", symbol))

    async def index_bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
        market: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "index_bars",
                symbol,
                frequency,
                start,
                offset,
                market,
            )
        )

    async def index_bars_until(
        self,
        symbol: str,
        predicate: BarPredicate,
        frequency: int | str = 9,
        market: int | None = None,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "index_bars_until",
                symbol,
                predicate,
                frequency,
                market,
                page_size,
                max_pages,
            )
        )

    async def index_bars_all(
        self,
        symbol: str,
        frequency: int | str = 9,
        market: int | None = None,
        page_size: int = BAR_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "index_bars_all",
                symbol,
                frequency,
                market,
                page_size,
                max_pages,
            )
        )

    async def block(self, block_file: str = "block.dat") -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "block", block_file))

    async def block_file_raw(self, block_file: str) -> bytes:
        return bytes(await asyncio.to_thread(self._call_sync, "block_file_raw", block_file))

    async def report_file(
        self,
        filename: str,
        max_bytes: int = DEFAULT_MAX_REPORT_FILE_SIZE,
    ) -> bytes:
        return bytes(await asyncio.to_thread(self._call_sync, "report_file", filename, max_bytes))

    async def zhb_files(self, refresh: bool = False) -> Mapping[str, bytes]:
        return await asyncio.to_thread(self._call_sync, "zhb_files", refresh)

    async def tdx_block_indexes(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "tdx_block_indexes", refresh))

    async def tdx_block_aliases(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "tdx_block_aliases", refresh))

    async def block_with_index(
        self,
        block_file: str = "block_gn.dat",
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(self._call_sync, "block_with_index", block_file, refresh)
        )

    async def sp_blocks(
        self,
        name: str | None = None,
        refresh: bool = False,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "sp_blocks", name, refresh))

    async def tdx_industries(self) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "tdx_industries"))

    async def ipo_subscriptions(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "ipo_subscriptions", refresh))

    async def stock_statistics(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "stock_statistics", refresh))

    async def stock_statistics2(self, refresh: bool = False) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "stock_statistics2", refresh))

    async def f10_categories(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "f10_categories", symbol))

    async def f10_content(self, symbol: str, name: str) -> str:
        return str(await asyncio.to_thread(self._call_sync, "f10_content", symbol, name))

    async def f10_content_range(
        self,
        symbol: str,
        filename: str,
        start: int,
        length: int,
    ) -> bytes:
        return bytes(
            await asyncio.to_thread(
                self._call_sync,
                "f10_content_range",
                symbol,
                filename,
                start,
                length,
            )
        )

    def _call_sync(self, api: str, *args: Any, **kwargs: Any) -> object:
        client = self._get_sync_client()
        if not hasattr(client, api):
            raise NotImplementedError(f"AsyncClient.request() does not support api: {api}")
        return getattr(client, api)(*args, **kwargs)

    def _get_sync_client(self) -> SyncClient:
        if self._explicit_sync_client is not None:
            return self._explicit_sync_client

        client = getattr(self._thread_local, "client", None)
        if client is None or client.closed:
            client = SyncClient(**self._sync_client_kwargs)
            self._thread_local.client = client
            with self._clients_lock:
                self._worker_clients.append(client)
                self._closed = False
        return client


def _get_index_market(symbol: str, market: int | None = None) -> int:
    normalized = symbol.strip().lower()
    prefix = normalized[:2]
    prefixed_market = {"sz": 0, "sh": 1, "bj": 2}.get(prefix)
    if prefixed_market is not None:
        if market is not None and int(market) != prefixed_market:
            raise InvalidSymbolError(f"symbol prefix {prefix} conflicts with market {market}")
        return prefixed_market
    if market is not None:
        normalized_market = int(market)
        if normalized_market not in {0, 1, 2}:
            raise UnsupportedMarketError(f"unsupported index market: {market}")
        return normalized_market
    code = normalize_symbol(normalized)
    if code.startswith("899"):
        return 2
    return 1 if code.startswith(("000", "88", "99")) else 0


def _parse_block_content(data: bytes | bytearray) -> list[dict[str, object]]:
    if len(data) < 386:
        raise ProtocolDecodeError(f"block file body too short: {len(data)}")

    pos = 384
    try:
        (num,) = struct.unpack("<H", data[pos : pos + 2])
    except struct.error as exc:
        raise ProtocolDecodeError("failed to decode block file count") from exc

    pos += 2
    expected_size = pos + num * 2813
    if len(data) != expected_size:
        raise ProtocolDecodeError(
            f"invalid block file size: expected {expected_size} bytes for {num} blocks, got {len(data)}"
        )

    rows: list[dict[str, object]] = []

    for block_index in range(num):
        block_name_raw = data[pos : pos + 9]
        pos += 9
        try:
            stock_count, block_type = struct.unpack("<HH", data[pos : pos + 4])
        except struct.error as exc:
            raise ProtocolDecodeError(f"failed to decode block {block_index} header") from exc
        pos += 4
        if stock_count > 400:
            raise ProtocolDecodeError(
                f"invalid stock count {stock_count} in block {block_index}; maximum is 400"
            )
        block_stock_begin = pos
        block_name = block_name_raw.decode("gbk", "ignore").rstrip("\x00")

        for code_index in range(stock_count):
            raw_code = data[pos : pos + 7]
            if len(raw_code) != 7:
                raise ProtocolDecodeError(f"block {block_index} code {code_index} is truncated")
            code = raw_code.decode("ascii", "ignore").rstrip("\x00")
            pos += 7
            rows.append(
                {
                    "blockname": block_name,
                    "block_type": block_type,
                    "code_index": code_index,
                    "code": code,
                }
            )

        pos = block_stock_begin + 2800

    return rows


def _validate_remote_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename cannot be blank")
    normalized = filename.strip().replace("\\", "/")
    encoded = normalized.encode("utf-8")
    if b"\x00" in encoded:
        raise ValueError("filename cannot contain NUL bytes")
    if len(encoded) > 100:
        raise ValueError("filename must fit within 100 UTF-8 bytes")
    return normalized
