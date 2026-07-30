from __future__ import annotations

import asyncio
from collections.abc import Callable
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


def _server_endpoints(candidates) -> list[ServerEndpoint]:
    return [
        ServerEndpoint(
            host=candidate.host,
            port=candidate.port,
            label=candidate.label,
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
    result = data.drop(columns=["volume"], errors="ignore").copy()
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

        injected = raw_client or engine_client
        if injected is not None:
            self.client = injected
        elif self.server is not None:
            host, port = self.server
            self.client = SyncClient(servers=[ServerEndpoint(host=host, port=port, label="std-next")])
        elif servers is not None:
            self.client = SyncClient(servers=list(servers))
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_hq_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = SyncClient(servers=_server_endpoints(candidates))
            else:
                self.client = SyncClient()
        else:
            self.client = SyncClient()

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

    def stock_all(self) -> pd.DataFrame:
        return pd.concat([self.stocks(0), self.stocks(1)], ignore_index=True)

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

    def index(self, symbol="000001", frequency=9, start=0, offset=800, market=None, **kwargs) -> pd.DataFrame:
        return self.index_bars(
            symbol=symbol,
            frequency=frequency,
            start=start,
            offset=offset,
            market=market,
            **kwargs,
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
        self._error_mapper = error_mapper

        injected = raw_client or engine_client
        if injected is not None:
            self.client = injected
        elif self.server is not None:
            host, port = self.server
            self.client = AsyncClient(servers=[ServerEndpoint(host=host, port=port, label="std-next")])
        elif servers is not None:
            self.client = AsyncClient(servers=list(servers))
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_hq_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = AsyncClient(servers=_server_endpoints(candidates))
            else:
                self.client = AsyncClient()
        else:
            self.client = AsyncClient()

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

    async def stock_all(self) -> pd.DataFrame:
        sh, sz = await asyncio.gather(self.stocks(0), self.stocks(1))
        return pd.concat([sh, sz], ignore_index=True)

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

    async def index(self, symbol="000001", frequency=9, start=0, offset=800, market=None, **kwargs) -> pd.DataFrame:
        return await self.index_bars(
            symbol=symbol,
            frequency=frequency,
            start=start,
            offset=offset,
            market=market,
            **kwargs,
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
