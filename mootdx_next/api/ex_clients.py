from __future__ import annotations

import asyncio
import threading
from typing import Any

from mootdx_next.constants import EX_HOSTS
from mootdx_next.constants import MAX_EX_INSTRUMENT_COUNT
from mootdx_next.constants import MAX_EX_KLINE_COUNT
from mootdx_next.constants import MAX_EX_QUOTE_LIST_COUNT
from mootdx_next.constants import MAX_EX_TRANSACTION_COUNT
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import TransportError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.ex_markets import ExMarket
from mootdx_next.ex_markets import ExMarketRegistry
from mootdx_next.ex_markets import ex_market_registry as default_ex_market_registry
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.interfaces import AbstractScheduler
from mootdx_next.interfaces import AbstractTransport
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.params import normalize_date
from mootdx_next.params import normalize_frequency
from mootdx_next.protocol import ExQuoteProtocol
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool
from mootdx_next.transport.constants import EX_SETUP_PAYLOADS
from mootdx_next.transport.socket_transport import SyncSocketTransport


def _default_ex_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in EX_HOSTS]


def _ex_transport_factory() -> SyncSocketTransport:
    return SyncSocketTransport(setup_payloads=EX_SETUP_PAYLOADS)


class ExSyncClient:
    """Native synchronous client for TDX extended markets (ExHq)."""

    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
        market_registry: ExMarketRegistry | None = None,
    ) -> None:
        self.transport = transport
        self.protocol = protocol or ExQuoteProtocol()
        self.max_retries = max_retries
        self.market_registry = market_registry or default_ex_market_registry
        self._closed = False
        self.connection_pool = connection_pool or ConnectionPool(
            transport_factory=transport.__class__ if transport is not None else _ex_transport_factory
        )
        self.scheduler = scheduler or ServerPool(
            servers=servers or _default_ex_servers(),
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
        if not hasattr(self, api) or api.startswith("_") or api == "request":
            raise NotImplementedError(f"ExSyncClient.request() does not support api: {api}")
        return getattr(self, api)(**kwargs)

    def markets(self, refresh: bool = False) -> list[dict[str, object]]:
        snapshot = self.market_registry.get(self._load_markets, refresh=bool(refresh))
        return [item.to_dict() for item in snapshot]

    def instrument_count(self) -> int:
        return int(self._request("instrument_count"))

    def instrument(self, start: int = 0, offset: int = 100) -> list[dict[str, object]]:
        self._validate_window(start, offset, MAX_EX_INSTRUMENT_COUNT)
        return list(self._request("instruments", start=start, count=offset))

    def instruments(self, page_size: int = 1000) -> list[dict[str, object]]:
        if page_size <= 0 or page_size > MAX_EX_INSTRUMENT_COUNT:
            raise ValueError(
                f"page_size must be between 1 and {MAX_EX_INSTRUMENT_COUNT}"
            )
        total = self.instrument_count()
        rows: list[dict[str, object]] = []
        for start in range(0, total, page_size):
            page = self.instrument(start=start, offset=min(page_size, total - start))
            rows.extend(page)
            if len(page) < min(page_size, total - start):
                break
        return rows

    def quote(
        self,
        market: int,
        symbol: str,
        *,
        market_category: int | None = None,
    ) -> dict[str, object] | None:
        resolved_market_category = self._resolve_market_category(market, market_category)
        result = self._request(
            "quote",
            market=int(market),
            code=str(symbol),
            market_category=resolved_market_category,
        )
        return None if result is None else dict(result)

    def quotes(
        self,
        market: int,
        category: int,
        start: int = 0,
        offset: int = MAX_EX_QUOTE_LIST_COUNT,
    ) -> list[dict[str, object]]:
        self._validate_window(start, offset, MAX_EX_QUOTE_LIST_COUNT)
        if int(category) not in {2, 3}:
            raise ValueError("category must be 2 (HK) or 3 (futures)")
        return list(
            self._request(
                "quotes",
                market=int(market),
                category=int(category),
                start=start,
                count=offset,
            )
        )

    def bars(
        self,
        market: int,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = MAX_EX_KLINE_COUNT,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        self._validate_window(start, offset, MAX_EX_KLINE_COUNT)
        category = normalize_frequency(frequency)
        resolved_market_category = self._resolve_market_category(market, market_category)
        return list(
            self._request(
                "bars",
                category=category,
                market=int(market),
                code=str(symbol),
                start=start,
                count=offset,
                market_category=resolved_market_category,
            )
        )

    def minute(
        self,
        market: int,
        symbol: str,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        resolved_market_category = self._resolve_market_category(market, market_category)
        return list(
            self._request(
                "minute",
                market=int(market),
                code=str(symbol),
                market_category=resolved_market_category,
            )
        )

    def minutes(
        self,
        market: int,
        symbol: str,
        date: str | int,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        trading_date = int(normalize_date(date))
        resolved_market_category = self._resolve_market_category(market, market_category)
        return list(
            self._request(
                "minutes",
                market=int(market),
                code=str(symbol),
                date=trading_date,
                market_category=resolved_market_category,
            )
        )

    def transaction(
        self,
        market: int,
        symbol: str,
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
    ) -> list[dict[str, object]]:
        self._validate_window(start, offset, MAX_EX_TRANSACTION_COUNT)
        return list(
            self._request(
                "transaction",
                market=int(market),
                code=str(symbol),
                start=start,
                count=offset,
            )
        )

    def transactions(
        self,
        market: int,
        symbol: str,
        date: str | int,
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
    ) -> list[dict[str, object]]:
        self._validate_window(start, offset, MAX_EX_TRANSACTION_COUNT)
        trading_date = int(normalize_date(date))
        return list(
            self._request(
                "transactions",
                market=int(market),
                code=str(symbol),
                date=trading_date,
                start=start,
                count=offset,
            )
        )

    def bars_range(
        self,
        market: int,
        symbol: str,
        start: str | int,
        end: str | int,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        start_date = int(normalize_date(start))
        end_date = int(normalize_date(end))
        if start_date > end_date:
            raise ValueError("start must be on or before end")
        resolved_market_category = self._resolve_market_category(market, market_category)
        return list(
            self._request(
                "bars_range",
                market=int(market),
                code=str(symbol),
                start_date=start_date,
                end_date=end_date,
                market_category=resolved_market_category,
            )
        )

    def _request(self, api: str, **kwargs: Any) -> object:
        context = RequestContext(api=api, params=dict(kwargs))
        payload = self.protocol.encode(api, **kwargs)
        envelope = self._send(context, payload)
        return self.protocol.decode(api, envelope, **kwargs)

    def _load_markets(self) -> tuple[ExMarket, ...]:
        rows = self._request("markets")
        return tuple(
            ExMarket(
                market=int(row["market"]),
                category=int(row["category"]),
                name=str(row.get("name", "")),
                short_name=str(row.get("short_name", "")),
            )
            for row in rows
        )

    def _resolve_market_category(self, market: int, category: int | None) -> int:
        if category is not None:
            result = int(category)
            if not 0 <= result <= 0xFF:
                raise ValueError("market_category must be between 0 and 255")
            return result
        item = self.market_registry.find(int(market))
        if item is None:
            self.market_registry.get(self._load_markets)
            item = self.market_registry.find(int(market))
        if item is None:
            raise UnsupportedMarketError(f"unknown extended market: {market}")
        return item.category

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

    @staticmethod
    def _validate_window(start: int, count: int, maximum: int) -> None:
        if start < 0 or start > 0x7FFFFFFF:
            raise ValueError("start must be between 0 and 2147483647")
        if count <= 0 or count > maximum:
            raise ValueError(f"offset must be between 1 and {maximum}")


class AsyncExClient:
    """Async ExHq wrapper with one independent sync client per worker thread."""

    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
        sync_client: ExSyncClient | None = None,
        market_registry: ExMarketRegistry | None = None,
    ) -> None:
        self._explicit_sync_client = sync_client
        self._thread_local = threading.local()
        self._clients_lock = threading.Lock()
        self._worker_clients: list[ExSyncClient] = []
        self._closed = False
        self._sync_client_kwargs = {
            "transport": transport,
            "protocol": protocol,
            "scheduler": scheduler,
            "connection_pool": connection_pool,
            "servers": servers,
            "max_retries": max_retries,
            "market_registry": market_registry,
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

    async def markets(self, refresh: bool = False) -> list[dict[str, object]]:
        if refresh:
            return list(await asyncio.to_thread(self._call_sync, "markets", refresh=True))
        return list(await asyncio.to_thread(self._call_sync, "markets"))

    async def instrument_count(self) -> int:
        return int(await asyncio.to_thread(self._call_sync, "instrument_count"))

    async def instrument(self, start: int = 0, offset: int = 100) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "instrument", start, offset))

    async def instruments(self, page_size: int = 1000) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "instruments", page_size))

    async def quote(
        self,
        market: int,
        symbol: str,
        *,
        market_category: int | None = None,
    ) -> dict[str, object] | None:
        kwargs = {} if market_category is None else {"market_category": market_category}
        result = await asyncio.to_thread(self._call_sync, "quote", market, symbol, **kwargs)
        return None if result is None else dict(result)

    async def quotes(
        self,
        market: int,
        category: int,
        start: int = 0,
        offset: int = MAX_EX_QUOTE_LIST_COUNT,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync, "quotes", market, category, start, offset
            )
        )

    async def bars(
        self,
        market: int,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = MAX_EX_KLINE_COUNT,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        kwargs = {} if market_category is None else {"market_category": market_category}
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "bars",
                market,
                symbol,
                frequency,
                start,
                offset,
                **kwargs,
            )
        )

    async def minute(
        self,
        market: int,
        symbol: str,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        kwargs = {} if market_category is None else {"market_category": market_category}
        return list(await asyncio.to_thread(self._call_sync, "minute", market, symbol, **kwargs))

    async def minutes(
        self,
        market: int,
        symbol: str,
        date: str | int,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        kwargs = {} if market_category is None else {"market_category": market_category}
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "minutes",
                market,
                symbol,
                date,
                **kwargs,
            )
        )

    async def transaction(
        self,
        market: int,
        symbol: str,
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync, "transaction", market, symbol, start, offset
            )
        )

    async def transactions(
        self,
        market: int,
        symbol: str,
        date: str | int,
        start: int = 0,
        offset: int = MAX_EX_TRANSACTION_COUNT,
    ) -> list[dict[str, object]]:
        return list(
            await asyncio.to_thread(
                self._call_sync, "transactions", market, symbol, date, start, offset
            )
        )

    async def bars_range(
        self,
        market: int,
        symbol: str,
        start: str | int,
        end: str | int,
        *,
        market_category: int | None = None,
    ) -> list[dict[str, object]]:
        kwargs = {} if market_category is None else {"market_category": market_category}
        return list(
            await asyncio.to_thread(
                self._call_sync,
                "bars_range",
                market,
                symbol,
                start,
                end,
                **kwargs,
            )
        )

    def _call_sync(self, api: str, *args: Any, **kwargs: Any) -> object:
        client = self._get_sync_client()
        return getattr(client, api)(*args, **kwargs)

    def _get_sync_client(self) -> ExSyncClient:
        if self._explicit_sync_client is not None:
            return self._explicit_sync_client
        client = getattr(self._thread_local, "client", None)
        if client is None or client.closed:
            client = ExSyncClient(**self._sync_client_kwargs)
            self._thread_local.client = client
            with self._clients_lock:
                self._worker_clients.append(client)
                self._closed = False
        return client


__all__ = ["AsyncExClient", "ExSyncClient"]
