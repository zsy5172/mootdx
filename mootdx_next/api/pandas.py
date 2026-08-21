from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from typing import NoReturn

import pandas as pd

from mootdx_next.adapters import bars_to_frame
from mootdx_next.adapters import block_to_frame
from mootdx_next.adapters import call_auction_to_frame
from mootdx_next.adapters import finance_to_frame
from mootdx_next.adapters import limit_prices_to_frame
from mootdx_next.adapters import minutes_to_frame
from mootdx_next.adapters import price_limit_to_frame
from mootdx_next.adapters import quotes_to_frame
from mootdx_next.adapters import stocks_to_frame
from mootdx_next.adapters import transaction_to_frame
from mootdx_next.adapters import transactions_to_frame
from mootdx_next.adapters import xdxr_to_frame
from mootdx_next.adapters import xdxr_by_date_to_frame
from mootdx_next.adjustments import AGGREGATED_FREQUENCIES
from mootdx_next.adjustments import AdjustmentService
from mootdx_next.adjustments import AsyncAdjustmentService
from mootdx_next.adjustments import normalize_adjustment
from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.candidates import get_hq_candidates
from mootdx_next.constants import MARKET_SH
from mootdx_next.errors import AdjustmentError
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import OutsideTradingSessionError
from mootdx_next.errors import UnknownF10CategoryError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import ServerEndpoint
from mootdx_next.mac import MacPeriod
from mootdx_next.params import normalize_date
from mootdx_next.params import normalize_frequency
from mootdx_next.symbols import get_stock_market

KLINE_PAGE_SIZE = 800
KLINE_MAX_PAGES = 100
KLINE_VALUE_COLUMNS = ["open", "close", "high", "low", "vol", "amount"]

ErrorMapper = Callable[[Exception], Exception]
VALIDATION_ERRORS = (
    AdjustmentError,
    InvalidDateError,
    InvalidFrequencyError,
    InvalidSymbolError,
    OutsideTradingSessionError,
    UnknownF10CategoryError,
    UnsupportedMarketError,
)


def normalize_server(server: object) -> tuple[str, int] | None:
    if server is None:
        return None
    if not isinstance(server, (tuple, list)) or len(server) != 2:
        raise ValueError('Server 格式错误. 例如: server = ("127.0.0.1", 7709)')
    host, port = server
    host = str(host).strip()
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError('Server 格式错误. 例如: server = ("127.0.0.1", 7709)') from exc
    if not host or not 1 <= port <= 65535:
        raise ValueError('Server 格式错误. 例如: server = ("127.0.0.1", 7709)')
    return host, port


def _timeout_to_ms(timeout: object) -> int:
    """Convert the legacy seconds-based timeout to the native millisecond unit."""

    try:
        seconds = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a positive number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    return max(1, int(round(seconds * 1000)))


def _server_endpoints(candidates) -> list[ServerEndpoint]:
    return [
        ServerEndpoint(
            host=candidate.host,
            port=candidate.port,
            label=candidate.label,
            capabilities=frozenset(candidate.capabilities),
        )
        for candidate in candidates
    ]


def _history_frame(
    code: str,
    start_date: str | datetime,
    end_date: str | datetime,
    frames: list[pd.DataFrame],
) -> pd.DataFrame:
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    if end <= start or not frames:
        return pd.DataFrame()

    data = pd.concat(frames, ignore_index=True)
    dates = pd.to_datetime(data["datetime"].astype(str).str[:10], errors="coerce")
    data = data.loc[dates.notna()].copy()
    data["date"] = dates.loc[dates.notna()]
    data["code"] = str(code)
    data.drop_duplicates(subset=["date"], keep="last", inplace=True)
    data.drop(
        columns=["year", "month", "day", "hour", "minute", "datetime"],
        errors="ignore",
        inplace=True,
    )
    data.set_index("date", inplace=True)
    return data.loc[(data.index >= start) & (data.index <= end)].sort_index()


def _get_k_data_shape(data: pd.DataFrame) -> pd.DataFrame:
    # These entry points intentionally preserve the legacy Quotes API shape.
    # ``previous_close`` belongs to the native next-engine bars API and must
    # not leak into get_k_data()/k()/ohlc() compatibility results.
    result = data.drop(columns=["volume", "previous_close"], errors="ignore").copy()
    base = [column for column in KLINE_VALUE_COLUMNS if column in result.columns]
    extras = [column for column in result.columns if column not in {*base, "code"}]
    trailing = ["code"] if "code" in result.columns else []
    return result[base + extras + trailing]


def _k_shape(data: pd.DataFrame) -> pd.DataFrame:
    result = _get_k_data_shape(data)
    if "vol" in result.columns:
        result = result.copy()
        result["volume"] = result["vol"].to_numpy(copy=False)
    return result


def _legacy_index_shape(data: pd.DataFrame) -> pd.DataFrame:
    """Remove native next metadata from the legacy ``index()`` result."""

    result = data.copy()
    if "volume_raw" in result.columns:
        # Legacy Quotes.index() exposed the unnormalised protocol slot.  The
        # native index_bars() API keeps the corrected lot/turnover semantics.
        result["vol"] = result["volume_raw"].to_numpy(copy=False)
        result["volume"] = result["volume_raw"].to_numpy(copy=False)
    return result.drop(
        columns=[
            "previous_close",
            "volume_raw",
            "volume_unit",
            "volume_lots",
            "turnover_100_yuan",
        ],
        errors="ignore",
    )


def _adjustment_factor_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    data = pd.DataFrame.from_records(rows)
    if "datetime" not in data.columns:
        return data
    timestamps = pd.to_datetime(data.pop("datetime"), errors="coerce")
    data.index = timestamps
    data.index.name = "datetime"
    return data.sort_index()


class PandasClient:
    """Pandas-oriented next SDK client with legacy-compatible public methods."""

    def __init__(
        self,
        server: tuple[str, int] | list[object] | None = None,
        bestip: bool = False,
        timeout: int = 15,
        heartbeat: bool = False,
        auto_retry: bool = True,
        raise_exception: bool = False,
        *,
        servers: list[ServerEndpoint] | None = None,
        raw_client: SyncClient | None = None,
        engine_client: SyncClient | None = None,
        error_mapper: ErrorMapper | None = None,
        **kwargs: Any,
    ) -> None:
        if raw_client is not None and engine_client is not None:
            raise ValueError("raw_client and engine_client are mutually exclusive")
        if server is not None and servers is not None:
            raise ValueError("server and servers are mutually exclusive")

        self.server = normalize_server(server)
        self.bestip = self.server
        self.timeout = timeout or 15
        self.verbose = bool(kwargs.get("verbose", False))
        self.heartbeat = heartbeat
        self.auto_retry = auto_retry
        self.raise_exception = raise_exception
        self._error_mapper = error_mapper

        client_options = {
            "timeout_ms": _timeout_to_ms(self.timeout),
            "max_retries": 1 if self.auto_retry else 0,
            "heartbeat": self.heartbeat,
        }

        injected = raw_client or engine_client
        if injected is not None:
            self.client = injected
        elif self.server is not None:
            host, port = self.server
            self.client = SyncClient(
                servers=[ServerEndpoint(host=host, port=port, label="std-next")],
                **client_options,
            )
        elif servers is not None:
            self.client = SyncClient(servers=list(servers), **client_options)
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_hq_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = SyncClient(servers=_server_endpoints(candidates), **client_options)
            else:
                self.client = SyncClient(**client_options)
        else:
            self.client = SyncClient(**client_options)

        self._adjustments = AdjustmentService(self.client)

    @property
    def raw_client(self) -> SyncClient:
        return self.client

    @property
    def closed(self) -> bool:
        return bool(getattr(self.client, "closed", False))

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def reconnect(self) -> None:
        if hasattr(self.client, "reconnect"):
            self.client.reconnect()

    def metrics(self):
        if hasattr(self.client, "connection_pool"):
            return self.client.connection_pool.snapshot()
        return None

    def traffic(self):
        return self.metrics()

    def quotes(self, symbol=None, **kwargs) -> pd.DataFrame:
        if not symbol:
            return pd.DataFrame()
        try:
            return quotes_to_frame(self.client.quotes(symbol=symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def _mac_frame(self, method: str, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(getattr(self.client, method)(*args, **kwargs))

    def mac_quotes(self, symbols=None, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_quotes", symbols, **kwargs)

    def mac_quotes_list(self, category=6, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_quotes_list", category, **kwargs)

    def mac_board_list(self, board_type=255, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_board_list", board_type, **kwargs)

    def mac_board_members(self, block, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_board_members", block, **kwargs)

    def mac_belong_board(self, symbol, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_belong_board", symbol, **kwargs)

    def mac_capital_flow(self, symbol, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_capital_flow", symbol, **kwargs)

    def mac_symbol_info(self, symbol, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_symbol_info", symbol, **kwargs)

    def mac_bars(self, symbol="000001", frequency=MacPeriod.DAY, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_bars", symbol, frequency, **kwargs)

    def mac_tick_chart(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_tick_chart", symbol, **kwargs)

    def mac_tick_charts(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_tick_charts", symbol, **kwargs)

    def mac_chart_sampling(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_chart_sampling", symbol, **kwargs)

    def mac_transactions(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_transactions", symbol, **kwargs)

    def mac_auction(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_auction", symbol, **kwargs)

    def mac_unusual(self, market=1, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_unusual", market, **kwargs)

    def mac_server_info(self, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_server_info", **kwargs)

    def mac_kline_offset(self, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_kline_offset", **kwargs)

    def mac_goods_list(self, market, **kwargs) -> pd.DataFrame:
        return self._mac_frame("mac_goods_list", market, **kwargs)

    def mac_file_meta(self, filename, **kwargs):
        return self.client.mac_file_meta(filename, **kwargs)

    def mac_file_chunk(self, filename, **kwargs) -> bytes:
        return self.client.mac_file_chunk(filename, **kwargs)

    def mac_file(self, filename, **kwargs) -> bytes:
        return self.client.mac_file(filename, **kwargs)

    def quotes_all(self, symbol=None, **kwargs) -> pd.DataFrame:
        if not symbol:
            return pd.DataFrame()
        try:
            return quotes_to_frame(self.client.quotes_all(symbol=symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def limit_prices(self, start=0, count=2000, **kwargs) -> pd.DataFrame:
        return limit_prices_to_frame(self.client.limit_prices(start=int(start), count=int(count)))

    def price_limit(self, symbol="", refresh=False, **kwargs) -> pd.DataFrame:
        try:
            return price_limit_to_frame(self.client.price_limit(str(symbol), refresh=bool(refresh)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def bars(self, symbol="000001", frequency=9, start=0, offset=800, **kwargs) -> pd.DataFrame:
        adjust = normalize_adjustment(kwargs.pop("adjust", None))
        try:
            normalized_frequency = normalize_frequency(frequency)
            normalized_offset = min(int(offset), KLINE_PAGE_SIZE)
            if adjust and normalized_frequency in AGGREGATED_FREQUENCIES:
                return self._adjustments.adjusted_bars(
                    str(symbol),
                    adjust,
                    frequency=normalized_frequency,
                    start=int(start),
                    offset=normalized_offset,
                )

            data = bars_to_frame(
                self.client.bars(
                    symbol=str(symbol),
                    frequency=normalized_frequency,
                    start=int(start),
                    offset=normalized_offset,
                )
            )
            if adjust:
                data = self._adjustments.apply(data, str(symbol), adjust)
            return data
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def bars_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        frequency=9,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.bars_until(
                    str(symbol),
                    predicate,
                    frequency=normalize_frequency(frequency),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def bars_all(
        self,
        symbol="000001",
        frequency=9,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.bars_all(
                    str(symbol),
                    frequency=normalize_frequency(frequency),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minute_bars_241(
        self,
        symbol="000001",
        start=0,
        offset=KLINE_PAGE_SIZE,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.minute_bars_241(
                    str(symbol),
                    start=int(start),
                    offset=int(offset),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minute_bars_241_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.minute_bars_241_until(
                    str(symbol),
                    predicate,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minute_bars_241_all(
        self,
        symbol="000001",
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.minute_bars_241_all(
                    str(symbol),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def stock_count(self, market=MARKET_SH) -> int:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误"))
        return self.client.stock_count(int(market))

    def stock_page(self, market=MARKET_SH, start=0, refresh=False) -> pd.DataFrame:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场"))
        return stocks_to_frame(
            self.client.stock_page(int(market), start=int(start), refresh=bool(refresh))
        )

    def stocks(self, market=MARKET_SH, refresh=False) -> pd.DataFrame:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场"))
        rows = (
            self.client.stocks(int(market), refresh=True)
            if refresh
            else self.client.stocks(int(market))
        )
        return stocks_to_frame(rows)

    def securities(self, refresh=False) -> pd.DataFrame:
        return stocks_to_frame(self.client.securities(refresh=bool(refresh)))

    def security(self, symbol="", refresh=False) -> pd.DataFrame:
        try:
            row = self.client.security(str(symbol), refresh=bool(refresh))
            return stocks_to_frame([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def stock_codes(self, refresh=False) -> list[str]:
        return self.client.stock_codes(refresh=bool(refresh))

    def etf_codes(self, refresh=False) -> list[str]:
        return self.client.etf_codes(refresh=bool(refresh))

    def index_codes(self, refresh=False) -> list[str]:
        return self.client.index_codes(refresh=bool(refresh))

    def stock_all(self, markets=(0, 1)) -> pd.DataFrame:
        normalized_markets = tuple(int(market) for market in markets)
        if not normalized_markets:
            return stocks_to_frame([])
        return pd.concat([self.stocks(market) for market in normalized_markets], ignore_index=True)

    def minute(self, symbol=None, **kwargs) -> pd.DataFrame:
        today = datetime.now().strftime("%Y%m%d")
        return self.minutes(symbol=symbol, date=today, **kwargs)

    def call_auction(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            return call_auction_to_frame(self.client.call_auction(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minutes(self, symbol=None, date="20191023", **kwargs) -> pd.DataFrame:
        adjust = normalize_adjustment(kwargs.pop("adjust", None))
        try:
            market = get_stock_market(symbol)
            if market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            normalized_date = normalize_date(date)
            data = minutes_to_frame(self.client.minutes(symbol=str(symbol), date=normalized_date))
            if adjust:
                data = self._adjustments.apply(data, str(symbol), adjust, as_of=normalized_date)
            return data
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transaction(self, symbol="", start=0, offset=800, **kwargs) -> pd.DataFrame:
        try:
            rows = self.client.transaction(symbol=str(symbol), start=int(start), offset=int(offset))
            return transaction_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transaction_all(
        self,
        symbol="",
        page_size=1800,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            rows = self.client.transaction_all(
                str(symbol),
                page_size=int(page_size),
                max_pages=None if max_pages is None else int(max_pages),
            )
            return transaction_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transactions(self, symbol="", start=0, offset=800, date="20170209", **kwargs) -> pd.DataFrame:
        try:
            market = get_stock_market(symbol)
            if market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            rows = self.client.transactions(
                symbol=str(symbol),
                start=int(start),
                offset=int(offset),
                date=date,
            )
            return transactions_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transactions_day(
        self,
        symbol="",
        date="20170209",
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            rows = self.client.transactions_day(
                str(symbol),
                date,
                page_size=int(page_size),
                max_pages=None if max_pages is None else int(max_pages),
            )
            return transactions_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def iter_transactions(
        self,
        symbol="",
        start_date="20170209",
        end_date="20170209",
        *,
        include_empty=False,
        trading_days_only=True,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        for date, rows in self.client.iter_transactions(
            str(symbol),
            start_date,
            end_date,
            include_empty=bool(include_empty),
            trading_days_only=bool(trading_days_only),
            refresh_calendar=bool(refresh_calendar),
            page_size=int(page_size),
            max_pages=None if max_pages is None else int(max_pages),
        ):
            yield date, transactions_to_frame(rows)

    def iter_transaction_history(
        self,
        symbol="",
        before=None,
        *,
        include_today=False,
        include_empty=False,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        for date, rows in self.client.iter_transaction_history(
            str(symbol),
            before,
            include_today=bool(include_today),
            include_empty=bool(include_empty),
            refresh_calendar=bool(refresh_calendar),
            page_size=int(page_size),
            max_pages=None if max_pages is None else int(max_pages),
        ):
            yield date, transactions_to_frame(rows)

    def trading_days(self, start_date=None, end_date=None, refresh=False) -> list[str]:
        return list(
            self.client.trading_days(
                start_date,
                end_date,
                refresh=bool(refresh),
            )
        )

    def is_trading_day(self, date, refresh=False) -> bool:
        return self.client.is_trading_day(date, refresh=bool(refresh))

    def trading_calendar(self, start_date=None, end_date=None, refresh=False) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            self.client.trading_calendar(
                start_date,
                end_date,
                refresh=bool(refresh),
            )
        )

    def f10_categories(self, symbol="", market=None) -> list[dict[str, object]]:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return self.client.f10_categories(str(symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def f10_content(self, symbol="", name="", market=None) -> str:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return self.client.f10_content(str(symbol), str(name))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def f10_content_range(self, symbol="", filename="", start=0, length=0, market=None) -> bytes:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return self.client.f10_content_range(
                str(symbol),
                str(filename),
                int(start),
                int(length),
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def F10C(self, symbol="", market=None):  # noqa: N802
        return self.f10_categories(symbol=symbol, market=market)

    def F10(self, symbol="", name="", market=None):  # noqa: N802
        categories = self.f10_categories(symbol=symbol, market=market)
        if not categories:
            return None
        if name:
            matched = next((item for item in categories if item["name"] == name), None)
            if matched is not None:
                return self.f10_content(symbol=symbol, name=matched["name"], market=market)
        return {
            item["name"]: self.f10_content(symbol=symbol, name=item["name"], market=market)
            for item in categories
        }

    def xdxr(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(self.client.xdxr(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def xdxr_by_date(self, symbol="", categories=None, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_by_date_to_frame(
                self.client.xdxr_by_date(str(symbol), categories)
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def gbbq_all(self, refresh=False, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(self.client.gbbq_all(refresh=bool(refresh)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def gbbq(self, symbol="", refresh=False, fallback=True, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(
                self.client.gbbq(
                    str(symbol),
                    refresh=bool(refresh),
                    fallback=bool(fallback),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def etf_pcf(self, code="", trading_date="", **kwargs) -> pd.DataFrame:
        try:
            return pd.DataFrame.from_records(
                self.client.etf_pcf(str(code), trading_date)
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def iter_xdxr(
        self,
        symbols=None,
        *,
        refresh=False,
        retries=1,
        **kwargs,
    ) -> Iterator[tuple[str, pd.DataFrame]]:
        for symbol, rows in self.client.iter_xdxr(
            symbols,
            refresh=bool(refresh),
            retries=int(retries),
        ):
            yield symbol, xdxr_to_frame(rows)

    def equity_at(self, symbol="", as_of="19700101", **kwargs) -> pd.DataFrame:
        try:
            row = self.client.equity_at(str(symbol), as_of)
            return pd.DataFrame.from_records([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def market_value(
        self,
        symbol="",
        as_of="19700101",
        price=0,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            row = self.client.market_value(str(symbol), as_of, price)
            return pd.DataFrame.from_records([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def turnover(
        self,
        symbol="",
        as_of="19700101",
        volume=0,
        volume_unit="shares",
        **kwargs,
    ) -> float | None:
        try:
            return self.client.turnover(
                str(symbol),
                as_of,
                volume,
                volume_unit=str(volume_unit),
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def adjustment_factors(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            if hasattr(self.client, "adjustment_factors"):
                return _adjustment_factor_frame(self.client.adjustment_factors(str(symbol)))
            return self._adjustments.factors(str(symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def finance(self, symbol="000001", **kwargs) -> pd.DataFrame:
        try:
            return finance_to_frame(self.client.finance(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def index_bars(
        self,
        symbol="000001",
        frequency=9,
        start=0,
        offset=800,
        market=None,
        **kwargs,
    ) -> pd.DataFrame:
        kwargs.pop("adjust", None)
        try:
            rows = self.client.index_bars(
                symbol=str(symbol),
                frequency=normalize_frequency(frequency),
                start=int(start),
                offset=min(int(offset), KLINE_PAGE_SIZE),
                market=market,
            )
            return bars_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def index_bars_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        frequency=9,
        market=None,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.index_bars_until(
                    str(symbol),
                    predicate,
                    frequency=normalize_frequency(frequency),
                    market=market,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def index_bars_all(
        self,
        symbol="000001",
        frequency=9,
        market=None,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                self.client.index_bars_all(
                    str(symbol),
                    frequency=normalize_frequency(frequency),
                    market=market,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def index(self, symbol="000001", frequency=9, start=0, offset=800, market=None, **kwargs) -> pd.DataFrame:
        return _legacy_index_shape(
            self.index_bars(
                symbol=symbol,
                frequency=frequency,
                start=start,
                offset=offset,
                market=market,
                **kwargs,
            )
        )

    def block(self, tofile="block.dat", **kwargs) -> pd.DataFrame:
        return block_to_frame(self.client.block(str(tofile)))

    def block_file_raw(self, filename="block.dat", **kwargs) -> bytes:
        return self.client.block_file_raw(str(filename))

    def report_file(self, filename="zhb.zip", max_bytes=None, **kwargs) -> bytes:
        if max_bytes is None:
            return self.client.report_file(str(filename))
        return self.client.report_file(str(filename), max_bytes=int(max_bytes))

    def zhb_files(self, refresh=False, **kwargs) -> Mapping[str, bytes]:
        return self.client.zhb_files(refresh=bool(refresh))

    def tdx_block_indexes(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.tdx_block_indexes(refresh=bool(refresh)))

    def tdx_block_aliases(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.tdx_block_aliases(refresh=bool(refresh)))

    def block_catalog(self, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            self.client.block_catalog(category=category, refresh=bool(refresh))
        )

    def block_members(self, block, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            self.client.block_members(str(block), category=category, refresh=bool(refresh))
        )

    def block_members_all(self, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            self.client.block_members_all(category=category, refresh=bool(refresh))
        )

    def tdx_block_base(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.tdx_block_base(refresh=bool(refresh)))

    def fund_flows(self, symbol=None, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.fund_flows(symbol=symbol))

    def historical_fund_flows(self, symbol, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            self.client.historical_fund_flows(symbol, **kwargs)
        )

    def block_with_index(self, tofile="block_gn.dat", refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            self.client.block_with_index(str(tofile), refresh=bool(refresh))
        )

    def sp_blocks(self, name=None, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            self.client.sp_blocks(name=name, refresh=bool(refresh))
        )

    def tdx_industries(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.tdx_industries())

    def ipo_subscriptions(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.ipo_subscriptions(refresh=bool(refresh)))

    def stock_statistics(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.stock_statistics(refresh=bool(refresh)))

    def stock_statistics2(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(self.client.stock_statistics2(refresh=bool(refresh)))

    def get_k_data(
        self,
        code: str,
        start_date: str | datetime,
        end_date: str | datetime,
        adjust: str | None = None,
    ) -> pd.DataFrame:
        normalized_adjustment = normalize_adjustment(adjust)
        if normalized_adjustment:
            try:
                return _get_k_data_shape(
                    self._adjusted_k_data(code, start_date, end_date, normalized_adjustment)
                )
            except VALIDATION_ERRORS as exc:
                self._raise_mapped(exc)

        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        if end <= start:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        previous_oldest: pd.Timestamp | None = None
        for page_index in range(KLINE_MAX_PAGES):
            rows = self.client.bars(
                symbol=str(code),
                frequency=9,
                start=page_index * KLINE_PAGE_SIZE,
                offset=KLINE_PAGE_SIZE,
            )
            frame = bars_to_frame(rows)
            if frame.empty:
                break
            frames.append(frame)
            oldest = frame.index.min()
            if oldest.normalize() <= start:
                break
            if len(frame) < KLINE_PAGE_SIZE:
                break
            if previous_oldest is not None and oldest >= previous_oldest:
                break
            previous_oldest = oldest
        return _get_k_data_shape(_history_frame(str(code), start, end, frames))

    def k(self, symbol="", begin=None, end=None, **kwargs) -> pd.DataFrame:
        adjust = kwargs.pop("adjust", None)
        return _k_shape(self.get_k_data(symbol, begin, end, adjust=adjust))

    def ohlc(self, **kwargs) -> pd.DataFrame:
        return self.k(**kwargs)

    def _adjusted_k_data(
        self,
        code: str,
        start_date: str | datetime,
        end_date: str | datetime,
        adjust: str,
    ) -> pd.DataFrame:
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        if end <= start:
            return pd.DataFrame()
        data = self._adjustments.adjusted_range(
            str(code),
            adjust,
            start=start,
            end=end,
        ).copy()
        data["code"] = str(code)
        data["date"] = data.index.normalize()
        data.drop(
            columns=["year", "month", "day", "hour", "minute", "datetime"],
            errors="ignore",
            inplace=True,
        )
        data.set_index("date", inplace=True)
        return data.loc[(data.index >= start) & (data.index <= end)].sort_index()

    def _raise_mapped(self, exc: Exception) -> NoReturn:
        if self._error_mapper is not None:
            raise self._error_mapper(exc) from exc
        raise exc


class AsyncPandasClient:
    """Async Pandas client with the same business methods as PandasClient."""

    def __init__(
        self,
        server: tuple[str, int] | list[object] | None = None,
        bestip: bool = False,
        timeout: int = 15,
        heartbeat: bool = False,
        auto_retry: bool = True,
        raise_exception: bool = False,
        *,
        servers: list[ServerEndpoint] | None = None,
        raw_client: AsyncClient | None = None,
        engine_client: AsyncClient | None = None,
        error_mapper: ErrorMapper | None = None,
        **kwargs: Any,
    ) -> None:
        if raw_client is not None and engine_client is not None:
            raise ValueError("raw_client and engine_client are mutually exclusive")
        if server is not None and servers is not None:
            raise ValueError("server and servers are mutually exclusive")

        self.server = normalize_server(server)
        self.bestip = self.server
        self.timeout = timeout or 15
        self.verbose = bool(kwargs.get("verbose", False))
        self.heartbeat = heartbeat
        self.auto_retry = auto_retry
        self.raise_exception = raise_exception
        self._error_mapper = error_mapper

        client_options = {
            "timeout_ms": _timeout_to_ms(self.timeout),
            "max_retries": 1 if self.auto_retry else 0,
            "heartbeat": self.heartbeat,
        }

        injected = raw_client or engine_client
        if injected is not None:
            self.client = injected
        elif self.server is not None:
            host, port = self.server
            self.client = AsyncClient(
                servers=[ServerEndpoint(host=host, port=port, label="std-next")],
                **client_options,
            )
        elif servers is not None:
            self.client = AsyncClient(servers=list(servers), **client_options)
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_hq_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = AsyncClient(servers=_server_endpoints(candidates), **client_options)
            else:
                self.client = AsyncClient(**client_options)
        else:
            self.client = AsyncClient(**client_options)

        self._adjustments = AsyncAdjustmentService(self.client)

    @property
    def raw_client(self) -> AsyncClient:
        return self.client

    @property
    def closed(self) -> bool:
        return bool(getattr(self.client, "closed", False))

    def close(self) -> None:
        self.client.close()

    def reconnect(self) -> None:
        self.client.reconnect()

    def metrics(self):
        explicit = getattr(self.client, "_explicit_sync_client", None)
        if explicit is not None and hasattr(explicit, "connection_pool"):
            return explicit.connection_pool.snapshot()
        return None

    def traffic(self):
        return self.metrics()

    async def quotes(self, symbol=None, **kwargs) -> pd.DataFrame:
        if not symbol:
            return pd.DataFrame()
        try:
            return quotes_to_frame(await self.client.quotes(symbol=symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def _mac_frame(self, method: str, *args, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(await getattr(self.client, method)(*args, **kwargs))

    async def mac_quotes(self, symbols=None, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_quotes", symbols, **kwargs)

    async def mac_quotes_list(self, category=6, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_quotes_list", category, **kwargs)

    async def mac_board_list(self, board_type=255, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_board_list", board_type, **kwargs)

    async def mac_board_members(self, block, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_board_members", block, **kwargs)

    async def mac_belong_board(self, symbol, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_belong_board", symbol, **kwargs)

    async def mac_capital_flow(self, symbol, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_capital_flow", symbol, **kwargs)

    async def mac_symbol_info(self, symbol, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_symbol_info", symbol, **kwargs)

    async def mac_bars(self, symbol="000001", frequency=MacPeriod.DAY, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_bars", symbol, frequency, **kwargs)

    async def mac_tick_chart(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_tick_chart", symbol, **kwargs)

    async def mac_tick_charts(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_tick_charts", symbol, **kwargs)

    async def mac_chart_sampling(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_chart_sampling", symbol, **kwargs)

    async def mac_transactions(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_transactions", symbol, **kwargs)

    async def mac_auction(self, symbol="000001", **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_auction", symbol, **kwargs)

    async def mac_unusual(self, market=1, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_unusual", market, **kwargs)

    async def mac_server_info(self, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_server_info", **kwargs)

    async def mac_kline_offset(self, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_kline_offset", **kwargs)

    async def mac_goods_list(self, market, **kwargs) -> pd.DataFrame:
        return await self._mac_frame("mac_goods_list", market, **kwargs)

    async def mac_file_meta(self, filename, **kwargs):
        return await self.client.mac_file_meta(filename, **kwargs)

    async def mac_file_chunk(self, filename, **kwargs) -> bytes:
        return await self.client.mac_file_chunk(filename, **kwargs)

    async def mac_file(self, filename, **kwargs) -> bytes:
        return await self.client.mac_file(filename, **kwargs)

    async def quotes_all(self, symbol=None, **kwargs) -> pd.DataFrame:
        if not symbol:
            return pd.DataFrame()
        try:
            return quotes_to_frame(await self.client.quotes_all(symbol=symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def limit_prices(self, start=0, count=2000, **kwargs) -> pd.DataFrame:
        return limit_prices_to_frame(await self.client.limit_prices(start=int(start), count=int(count)))

    async def price_limit(self, symbol="", refresh=False, **kwargs) -> pd.DataFrame:
        try:
            return price_limit_to_frame(await self.client.price_limit(str(symbol), refresh=bool(refresh)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def bars(self, symbol="000001", frequency=9, start=0, offset=800, **kwargs) -> pd.DataFrame:
        adjust = normalize_adjustment(kwargs.pop("adjust", None))
        try:
            normalized_frequency = normalize_frequency(frequency)
            normalized_offset = min(int(offset), KLINE_PAGE_SIZE)
            if adjust and normalized_frequency in AGGREGATED_FREQUENCIES:
                return await self._adjustments.adjusted_bars(
                    str(symbol),
                    adjust,
                    frequency=normalized_frequency,
                    start=int(start),
                    offset=normalized_offset,
                )
            data = bars_to_frame(
                await self.client.bars(
                    symbol=str(symbol),
                    frequency=normalized_frequency,
                    start=int(start),
                    offset=normalized_offset,
                )
            )
            if adjust:
                data = await self._adjustments.apply(data, str(symbol), adjust)
            return data
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def bars_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        frequency=9,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.bars_until(
                    str(symbol),
                    predicate,
                    frequency=normalize_frequency(frequency),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def bars_all(
        self,
        symbol="000001",
        frequency=9,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.bars_all(
                    str(symbol),
                    frequency=normalize_frequency(frequency),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minute_bars_241(
        self,
        symbol="000001",
        start=0,
        offset=KLINE_PAGE_SIZE,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.minute_bars_241(
                    str(symbol),
                    start=int(start),
                    offset=int(offset),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minute_bars_241_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.minute_bars_241_until(
                    str(symbol),
                    predicate,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minute_bars_241_all(
        self,
        symbol="000001",
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        transaction_max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.minute_bars_241_all(
                    str(symbol),
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                    transaction_max_pages=(
                        None if transaction_max_pages is None else int(transaction_max_pages)
                    ),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def stock_count(self, market=MARKET_SH) -> int:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误"))
        return await self.client.stock_count(int(market))

    async def stock_page(self, market=MARKET_SH, start=0, refresh=False) -> pd.DataFrame:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场"))
        return stocks_to_frame(
            await self.client.stock_page(int(market), start=int(start), refresh=bool(refresh))
        )

    async def stocks(self, market=MARKET_SH, refresh=False) -> pd.DataFrame:
        if market not in {0, 1, 2}:
            self._raise_mapped(UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场"))
        rows = (
            await self.client.stocks(int(market), refresh=True)
            if refresh
            else await self.client.stocks(int(market))
        )
        return stocks_to_frame(rows)

    async def securities(self, refresh=False) -> pd.DataFrame:
        return stocks_to_frame(await self.client.securities(refresh=bool(refresh)))

    async def security(self, symbol="", refresh=False) -> pd.DataFrame:
        try:
            row = await self.client.security(str(symbol), refresh=bool(refresh))
            return stocks_to_frame([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def stock_codes(self, refresh=False) -> list[str]:
        return await self.client.stock_codes(refresh=bool(refresh))

    async def etf_codes(self, refresh=False) -> list[str]:
        return await self.client.etf_codes(refresh=bool(refresh))

    async def index_codes(self, refresh=False) -> list[str]:
        return await self.client.index_codes(refresh=bool(refresh))

    async def stock_all(self, markets=(0, 1)) -> pd.DataFrame:
        normalized_markets = tuple(int(market) for market in markets)
        if not normalized_markets:
            return stocks_to_frame([])
        frames = await asyncio.gather(*(self.stocks(market) for market in normalized_markets))
        return pd.concat(frames, ignore_index=True)

    async def minute(self, symbol=None, **kwargs) -> pd.DataFrame:
        today = datetime.now().strftime("%Y%m%d")
        return await self.minutes(symbol=symbol, date=today, **kwargs)

    async def call_auction(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            return call_auction_to_frame(await self.client.call_auction(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minutes(self, symbol=None, date="20191023", **kwargs) -> pd.DataFrame:
        adjust = normalize_adjustment(kwargs.pop("adjust", None))
        try:
            market = get_stock_market(symbol)
            if market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            normalized_date = normalize_date(date)
            data = minutes_to_frame(await self.client.minutes(symbol=str(symbol), date=normalized_date))
            if adjust:
                data = await self._adjustments.apply(
                    data,
                    str(symbol),
                    adjust,
                    as_of=normalized_date,
                )
            return data
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transaction(self, symbol="", start=0, offset=800, **kwargs) -> pd.DataFrame:
        try:
            rows = await self.client.transaction(symbol=str(symbol), start=int(start), offset=int(offset))
            return transaction_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transaction_all(
        self,
        symbol="",
        page_size=1800,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            rows = await self.client.transaction_all(
                str(symbol),
                page_size=int(page_size),
                max_pages=None if max_pages is None else int(max_pages),
            )
            return transaction_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transactions(self, symbol="", start=0, offset=800, date="20170209", **kwargs) -> pd.DataFrame:
        try:
            market = get_stock_market(symbol)
            if market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            rows = await self.client.transactions(
                symbol=str(symbol),
                start=int(start),
                offset=int(offset),
                date=date,
            )
            return transactions_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transactions_day(
        self,
        symbol="",
        date="20170209",
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            rows = await self.client.transactions_day(
                str(symbol),
                date,
                page_size=int(page_size),
                max_pages=None if max_pages is None else int(max_pages),
            )
            return transactions_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def iter_transactions(
        self,
        symbol="",
        start_date="20170209",
        end_date="20170209",
        *,
        include_empty=False,
        trading_days_only=True,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> AsyncIterator[tuple[str, pd.DataFrame]]:
        async for date, rows in self.client.iter_transactions(
            str(symbol),
            start_date,
            end_date,
            include_empty=bool(include_empty),
            trading_days_only=bool(trading_days_only),
            refresh_calendar=bool(refresh_calendar),
            page_size=int(page_size),
            max_pages=None if max_pages is None else int(max_pages),
        ):
            yield date, transactions_to_frame(rows)

    async def iter_transaction_history(
        self,
        symbol="",
        before=None,
        *,
        include_today=False,
        include_empty=False,
        refresh_calendar=False,
        page_size=2000,
        max_pages=None,
        **kwargs,
    ) -> AsyncIterator[tuple[str, pd.DataFrame]]:
        async for date, rows in self.client.iter_transaction_history(
            str(symbol),
            before,
            include_today=bool(include_today),
            include_empty=bool(include_empty),
            refresh_calendar=bool(refresh_calendar),
            page_size=int(page_size),
            max_pages=None if max_pages is None else int(max_pages),
        ):
            yield date, transactions_to_frame(rows)

    async def trading_days(self, start_date=None, end_date=None, refresh=False) -> list[str]:
        return list(
            await self.client.trading_days(
                start_date,
                end_date,
                refresh=bool(refresh),
            )
        )

    async def is_trading_day(self, date, refresh=False) -> bool:
        return await self.client.is_trading_day(date, refresh=bool(refresh))

    async def trading_calendar(self, start_date=None, end_date=None, refresh=False) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.trading_calendar(
                start_date,
                end_date,
                refresh=bool(refresh),
            )
        )

    async def f10_categories(self, symbol="", market=None) -> list[dict[str, object]]:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return await self.client.f10_categories(str(symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def f10_content(self, symbol="", name="", market=None) -> str:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return await self.client.f10_content(str(symbol), str(name))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def f10_content_range(
        self,
        symbol="",
        filename="",
        start=0,
        length=0,
        market=None,
    ) -> bytes:
        try:
            resolved_market = get_stock_market(symbol) if market is None else market
            if resolved_market not in {0, 1, 2}:
                raise UnsupportedMarketError("市场代码错误, 目前只支持沪深北市场")
            return await self.client.f10_content_range(
                str(symbol),
                str(filename),
                int(start),
                int(length),
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def F10C(self, symbol="", market=None):  # noqa: N802
        return await self.f10_categories(symbol=symbol, market=market)

    async def F10(self, symbol="", name="", market=None):  # noqa: N802
        categories = await self.f10_categories(symbol=symbol, market=market)
        if not categories:
            return None
        if name:
            matched = next((item for item in categories if item["name"] == name), None)
            if matched is not None:
                return await self.f10_content(symbol=symbol, name=matched["name"], market=market)
        contents = await asyncio.gather(
            *(self.f10_content(symbol=symbol, name=item["name"], market=market) for item in categories)
        )
        return {item["name"]: content for item, content in zip(categories, contents, strict=True)}

    async def xdxr(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(await self.client.xdxr(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def xdxr_by_date(self, symbol="", categories=None, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_by_date_to_frame(
                await self.client.xdxr_by_date(str(symbol), categories)
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def gbbq_all(self, refresh=False, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(await self.client.gbbq_all(refresh=bool(refresh)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def gbbq(self, symbol="", refresh=False, fallback=True, **kwargs) -> pd.DataFrame:
        try:
            return xdxr_to_frame(
                await self.client.gbbq(
                    str(symbol),
                    refresh=bool(refresh),
                    fallback=bool(fallback),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def etf_pcf(self, code="", trading_date="", **kwargs) -> pd.DataFrame:
        try:
            return pd.DataFrame.from_records(
                await self.client.etf_pcf(str(code), trading_date)
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def iter_xdxr(
        self,
        symbols=None,
        *,
        refresh=False,
        retries=1,
        **kwargs,
    ) -> AsyncIterator[tuple[str, pd.DataFrame]]:
        async for symbol, rows in self.client.iter_xdxr(
            symbols,
            refresh=bool(refresh),
            retries=int(retries),
        ):
            yield symbol, xdxr_to_frame(rows)

    async def equity_at(self, symbol="", as_of="19700101", **kwargs) -> pd.DataFrame:
        try:
            row = await self.client.equity_at(str(symbol), as_of)
            return pd.DataFrame.from_records([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def market_value(
        self,
        symbol="",
        as_of="19700101",
        price=0,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            row = await self.client.market_value(str(symbol), as_of, price)
            return pd.DataFrame.from_records([] if row is None else [row])
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def turnover(
        self,
        symbol="",
        as_of="19700101",
        volume=0,
        volume_unit="shares",
        **kwargs,
    ) -> float | None:
        try:
            return await self.client.turnover(
                str(symbol),
                as_of,
                volume,
                volume_unit=str(volume_unit),
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def adjustment_factors(self, symbol="", **kwargs) -> pd.DataFrame:
        try:
            if hasattr(self.client, "adjustment_factors"):
                return _adjustment_factor_frame(
                    await self.client.adjustment_factors(str(symbol))
                )
            return await self._adjustments.factors(str(symbol))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def finance(self, symbol="000001", **kwargs) -> pd.DataFrame:
        try:
            return finance_to_frame(await self.client.finance(str(symbol)))
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def index_bars(
        self,
        symbol="000001",
        frequency=9,
        start=0,
        offset=800,
        market=None,
        **kwargs,
    ) -> pd.DataFrame:
        kwargs.pop("adjust", None)
        try:
            rows = await self.client.index_bars(
                symbol=str(symbol),
                frequency=normalize_frequency(frequency),
                start=int(start),
                offset=min(int(offset), KLINE_PAGE_SIZE),
                market=market,
            )
            return bars_to_frame(rows)
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def index_bars_until(
        self,
        symbol="000001",
        predicate=lambda _row: False,
        frequency=9,
        market=None,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.index_bars_until(
                    str(symbol),
                    predicate,
                    frequency=normalize_frequency(frequency),
                    market=market,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def index_bars_all(
        self,
        symbol="000001",
        frequency=9,
        market=None,
        page_size=KLINE_PAGE_SIZE,
        max_pages=None,
        **kwargs,
    ) -> pd.DataFrame:
        try:
            return bars_to_frame(
                await self.client.index_bars_all(
                    str(symbol),
                    frequency=normalize_frequency(frequency),
                    market=market,
                    page_size=int(page_size),
                    max_pages=None if max_pages is None else int(max_pages),
                )
            )
        except VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def index(self, symbol="000001", frequency=9, start=0, offset=800, market=None, **kwargs) -> pd.DataFrame:
        return _legacy_index_shape(
            await self.index_bars(
                symbol=symbol,
                frequency=frequency,
                start=start,
                offset=offset,
                market=market,
                **kwargs,
            )
        )

    async def block(self, tofile="block.dat", **kwargs) -> pd.DataFrame:
        return block_to_frame(await self.client.block(str(tofile)))

    async def block_file_raw(self, filename="block.dat", **kwargs) -> bytes:
        return await self.client.block_file_raw(str(filename))

    async def report_file(self, filename="zhb.zip", max_bytes=None, **kwargs) -> bytes:
        if max_bytes is None:
            return await self.client.report_file(str(filename))
        return await self.client.report_file(str(filename), max_bytes=int(max_bytes))

    async def zhb_files(self, refresh=False, **kwargs) -> Mapping[str, bytes]:
        return await self.client.zhb_files(refresh=bool(refresh))

    async def tdx_block_indexes(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.tdx_block_indexes(refresh=bool(refresh))
        )

    async def tdx_block_aliases(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.tdx_block_aliases(refresh=bool(refresh))
        )

    async def block_catalog(self, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.block_catalog(category=category, refresh=bool(refresh))
        )

    async def block_members(self, block, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            await self.client.block_members(str(block), category=category, refresh=bool(refresh))
        )

    async def block_members_all(self, category=None, refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            await self.client.block_members_all(category=category, refresh=bool(refresh))
        )

    async def tdx_block_base(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.tdx_block_base(refresh=bool(refresh))
        )

    async def fund_flows(self, symbol=None, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(await self.client.fund_flows(symbol=symbol))

    async def historical_fund_flows(self, symbol, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.historical_fund_flows(symbol, **kwargs)
        )

    async def block_with_index(self, tofile="block_gn.dat", refresh=False, **kwargs) -> pd.DataFrame:
        return block_to_frame(
            await self.client.block_with_index(str(tofile), refresh=bool(refresh))
        )

    async def sp_blocks(self, name=None, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.sp_blocks(name=name, refresh=bool(refresh))
        )

    async def tdx_industries(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(await self.client.tdx_industries())

    async def ipo_subscriptions(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.ipo_subscriptions(refresh=bool(refresh))
        )

    async def stock_statistics(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.stock_statistics(refresh=bool(refresh))
        )

    async def stock_statistics2(self, refresh=False, **kwargs) -> pd.DataFrame:
        return pd.DataFrame.from_records(
            await self.client.stock_statistics2(refresh=bool(refresh))
        )

    async def get_k_data(
        self,
        code: str,
        start_date: str | datetime,
        end_date: str | datetime,
        adjust: str | None = None,
    ) -> pd.DataFrame:
        normalized_adjustment = normalize_adjustment(adjust)
        if normalized_adjustment:
            try:
                start = pd.to_datetime(start_date)
                end = pd.to_datetime(end_date)
                if end <= start:
                    return pd.DataFrame()
                data = (
                    await self._adjustments.adjusted_range(
                        str(code),
                        normalized_adjustment,
                        start=start,
                        end=end,
                    )
                ).copy()
                data["code"] = str(code)
                data["date"] = data.index.normalize()
                data.drop(
                    columns=["year", "month", "day", "hour", "minute", "datetime"],
                    errors="ignore",
                    inplace=True,
                )
                data.set_index("date", inplace=True)
                return _get_k_data_shape(
                    data.loc[(data.index >= start) & (data.index <= end)].sort_index()
                )
            except VALIDATION_ERRORS as exc:
                self._raise_mapped(exc)

        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        if end <= start:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        previous_oldest: pd.Timestamp | None = None
        for page_index in range(KLINE_MAX_PAGES):
            rows = await self.client.bars(
                symbol=str(code),
                frequency=9,
                start=page_index * KLINE_PAGE_SIZE,
                offset=KLINE_PAGE_SIZE,
            )
            frame = bars_to_frame(rows)
            if frame.empty:
                break
            frames.append(frame)
            oldest = frame.index.min()
            if oldest.normalize() <= start:
                break
            if len(frame) < KLINE_PAGE_SIZE:
                break
            if previous_oldest is not None and oldest >= previous_oldest:
                break
            previous_oldest = oldest
        return _get_k_data_shape(_history_frame(str(code), start, end, frames))

    async def k(self, symbol="", begin=None, end=None, **kwargs) -> pd.DataFrame:
        adjust = kwargs.pop("adjust", None)
        return _k_shape(await self.get_k_data(symbol, begin, end, adjust=adjust))

    async def ohlc(self, **kwargs) -> pd.DataFrame:
        return await self.k(**kwargs)

    def _raise_mapped(self, exc: Exception) -> NoReturn:
        if self._error_mapper is not None:
            raise self._error_mapper(exc) from exc
        raise exc
