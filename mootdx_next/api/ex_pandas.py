from __future__ import annotations

from typing import Any
from typing import NoReturn

import pandas as pd

from mootdx_next.adapters import ex_bars_to_frame
from mootdx_next.adapters import ex_instruments_to_frame
from mootdx_next.adapters import ex_markets_to_frame
from mootdx_next.adapters import ex_minutes_to_frame
from mootdx_next.adapters import ex_quote_to_frame
from mootdx_next.adapters import ex_quotes_to_frame
from mootdx_next.adapters import ex_transactions_to_frame
from mootdx_next.api.ex_clients import AsyncExClient
from mootdx_next.api.ex_clients import ExSyncClient
from mootdx_next.candidates import get_ex_candidates
from mootdx_next.constants import MAX_EX_KLINE_COUNT
from mootdx_next.constants import MAX_EX_QUOTE_LIST_COUNT
from mootdx_next.constants import MAX_EX_TRANSACTION_COUNT
from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.models import ServerEndpoint

from .pandas import ErrorMapper
from .pandas import normalize_server
from .pandas import _server_endpoints
from .pandas import _timeout_to_ms

EX_VALIDATION_ERRORS = (
    InvalidDateError,
    InvalidFrequencyError,
    InvalidSymbolError,
    UnsupportedMarketError,
)


def _normalize_ex_symbol(market: object, symbol: object) -> tuple[int, str]:
    raw_symbol = str(symbol or "").strip()
    raw_market = market
    if "#" in raw_symbol:
        prefix, separator, bare_symbol = raw_symbol.partition("#")
        if not separator or not prefix or not bare_symbol or "#" in bare_symbol:
            raise InvalidSymbolError("扩展市场代码格式应为 market#symbol")
        if raw_market not in {None, ""} and int(raw_market) != int(prefix):
            raise InvalidSymbolError("market 参数与 symbol 市场前缀不一致")
        raw_market = prefix
        raw_symbol = bare_symbol

    if raw_market in {None, ""}:
        raise UnsupportedMarketError("市场参数不能为空")
    try:
        normalized_market = int(raw_market)
    except (TypeError, ValueError) as exc:
        raise UnsupportedMarketError(f"扩展市场代码无效: {raw_market}") from exc
    if not 0 <= normalized_market <= 255:
        raise UnsupportedMarketError("扩展市场代码必须在 0 到 255 之间")
    if not raw_symbol:
        raise InvalidSymbolError("扩展市场证券代码不能为空")
    try:
        encoded = raw_symbol.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidSymbolError("扩展市场证券代码必须为 ASCII") from exc
    if len(encoded) > 9:
        raise InvalidSymbolError("扩展市场证券代码不能超过 9 个字节")
    return normalized_market, raw_symbol


class ExPandasClient:
    """Pandas-oriented ExHq client with legacy-compatible method names."""

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
        raw_client: ExSyncClient | None = None,
        engine_client: ExSyncClient | None = None,
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
            self.client = ExSyncClient(
                servers=[ServerEndpoint(host=host, port=port, label="ex-next")],
                **client_options,
            )
        elif servers is not None:
            self.client = ExSyncClient(servers=list(servers), **client_options)
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_ex_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = ExSyncClient(servers=_server_endpoints(candidates), **client_options)
            else:
                self.client = ExSyncClient(**client_options)
        else:
            self.client = ExSyncClient(**client_options)

    @property
    def raw_client(self) -> ExSyncClient:
        return self.client

    @property
    def closed(self) -> bool:
        return bool(getattr(self.client, "closed", False))

    def close(self) -> None:
        self.client.close()

    def reconnect(self) -> None:
        self.client.reconnect()

    def metrics(self):
        if hasattr(self.client, "connection_pool"):
            return self.client.connection_pool.snapshot()
        return None

    def traffic(self):
        return self.metrics()

    @staticmethod
    def validate(market: object, symbol: object) -> tuple[int, str]:
        return _normalize_ex_symbol(market, symbol)

    def markets(self, refresh: bool = False, **kwargs: Any) -> pd.DataFrame:
        rows = self.client.markets(refresh=True) if refresh else self.client.markets()
        return ex_markets_to_frame(rows)

    def instrument(self, start: int = 0, offset: int = 100, **kwargs: Any) -> pd.DataFrame:
        return ex_instruments_to_frame(self.client.instrument(int(start), int(offset)))

    def instrument_count(self) -> int:
        return self.client.instrument_count()

    def instruments(self, page_size: int = 1000, **kwargs: Any) -> pd.DataFrame:
        return ex_instruments_to_frame(self.client.instruments(int(page_size)))

    def quote(self, market: object = "", symbol: object = "", **kwargs: Any) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_quote_to_frame(self.client.quote(normalized_market, code, **context))
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def quotes(
        self,
        market: int,
        category: int,
        start: int = 0,
        offset: int = MAX_EX_QUOTE_LIST_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return ex_quotes_to_frame(
            self.client.quotes(int(market), int(category), int(start), int(offset))
        )

    def bars(
        self,
        frequency: int | str = 9,
        market: object = "",
        symbol: object = "",
        start: int = 0,
        offset: int = MAX_EX_KLINE_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_bars_to_frame(
                self.client.bars(
                    normalized_market,
                    code,
                    frequency,
                    int(start),
                    int(offset),
                    **context,
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minute(self, market: object = "", symbol: object = "", **kwargs: Any) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_minutes_to_frame(self.client.minute(normalized_market, code, **context))
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def minutes(
        self,
        market: object = "",
        symbol: object = "",
        date: str | int = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_minutes_to_frame(self.client.minutes(normalized_market, code, date, **context))
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transaction(
        self,
        market: object = "",
        symbol: object = "",
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            return ex_transactions_to_frame(
                self.client.transaction(normalized_market, code, int(start), int(offset))
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def transactions(
        self,
        market: object = "",
        symbol: object = "",
        date: str | int = "",
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            return ex_transactions_to_frame(
                self.client.transactions(
                    normalized_market,
                    code,
                    date,
                    int(start),
                    int(offset),
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def bars_range(
        self,
        market: object = "",
        symbol: object = "",
        start: str | int = "",
        end: str | int = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_bars_to_frame(
                self.client.bars_range(
                    normalized_market,
                    code,
                    start,
                    end,
                    **context,
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def _raise_mapped(self, exc: Exception) -> NoReturn:
        if self._error_mapper is not None:
            raise self._error_mapper(exc) from exc
        raise exc


class AsyncExPandasClient:
    """Async Pandas ExHq client with method parity to ExPandasClient."""

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
        raw_client: AsyncExClient | None = None,
        engine_client: AsyncExClient | None = None,
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
            self.client = AsyncExClient(
                servers=[ServerEndpoint(host=host, port=port, label="ex-next")],
                **client_options,
            )
        elif servers is not None:
            self.client = AsyncExClient(servers=list(servers), **client_options)
            if servers:
                self.bestip = (servers[0].host, servers[0].port)
        elif bestip:
            candidates = get_ex_candidates()
            if candidates:
                self.bestip = (candidates[0].host, candidates[0].port)
                self.client = AsyncExClient(servers=_server_endpoints(candidates), **client_options)
            else:
                self.client = AsyncExClient(**client_options)
        else:
            self.client = AsyncExClient(**client_options)

    @property
    def raw_client(self) -> AsyncExClient:
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
        if explicit is not None:
            return explicit.connection_pool.snapshot()
        return None

    def traffic(self):
        return self.metrics()

    @staticmethod
    def validate(market: object, symbol: object) -> tuple[int, str]:
        return _normalize_ex_symbol(market, symbol)

    async def markets(self, refresh: bool = False, **kwargs: Any) -> pd.DataFrame:
        rows = await self.client.markets(refresh=True) if refresh else await self.client.markets()
        return ex_markets_to_frame(rows)

    async def instrument(self, start: int = 0, offset: int = 100, **kwargs: Any) -> pd.DataFrame:
        return ex_instruments_to_frame(await self.client.instrument(int(start), int(offset)))

    async def instrument_count(self) -> int:
        return await self.client.instrument_count()

    async def instruments(self, page_size: int = 1000, **kwargs: Any) -> pd.DataFrame:
        return ex_instruments_to_frame(await self.client.instruments(int(page_size)))

    async def quote(self, market: object = "", symbol: object = "", **kwargs: Any) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_quote_to_frame(await self.client.quote(normalized_market, code, **context))
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def quotes(
        self,
        market: int,
        category: int,
        start: int = 0,
        offset: int = MAX_EX_QUOTE_LIST_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return ex_quotes_to_frame(
            await self.client.quotes(int(market), int(category), int(start), int(offset))
        )

    async def bars(
        self,
        frequency: int | str = 9,
        market: object = "",
        symbol: object = "",
        start: int = 0,
        offset: int = MAX_EX_KLINE_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_bars_to_frame(
                await self.client.bars(
                    normalized_market,
                    code,
                    frequency,
                    int(start),
                    int(offset),
                    **context,
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minute(self, market: object = "", symbol: object = "", **kwargs: Any) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_minutes_to_frame(await self.client.minute(normalized_market, code, **context))
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def minutes(
        self,
        market: object = "",
        symbol: object = "",
        date: str | int = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_minutes_to_frame(
                await self.client.minutes(normalized_market, code, date, **context)
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transaction(
        self,
        market: object = "",
        symbol: object = "",
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            return ex_transactions_to_frame(
                await self.client.transaction(
                    normalized_market,
                    code,
                    int(start),
                    int(offset),
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def transactions(
        self,
        market: object = "",
        symbol: object = "",
        date: str | int = "",
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            return ex_transactions_to_frame(
                await self.client.transactions(
                    normalized_market,
                    code,
                    date,
                    int(start),
                    int(offset),
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    async def bars_range(
        self,
        market: object = "",
        symbol: object = "",
        start: str | int = "",
        end: str | int = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        try:
            normalized_market, code = self.validate(market, symbol)
            market_category = kwargs.get("market_category")
            context = {} if market_category is None else {"market_category": market_category}
            return ex_bars_to_frame(
                await self.client.bars_range(
                    normalized_market,
                    code,
                    start,
                    end,
                    **context,
                )
            )
        except EX_VALIDATION_ERRORS as exc:
            self._raise_mapped(exc)

    def _raise_mapped(self, exc: Exception) -> NoReturn:
        if self._error_mapper is not None:
            raise self._error_mapper(exc) from exc
        raise exc


__all__ = ["AsyncExPandasClient", "ExPandasClient"]
