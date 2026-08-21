from __future__ import annotations

import asyncio
import threading
from typing import Any

from mootdx_next.constants import EX_HOSTS
from mootdx_next.constants import DEFAULT_REQUEST_TIMEOUT_MS
from mootdx_next.constants import MAX_EX_INSTRUMENT_COUNT
from mootdx_next.constants import MAX_EX_KLINE_COUNT
from mootdx_next.constants import MAX_EX_QUOTE_LIST_COUNT
from mootdx_next.constants import MAX_EX_TRANSACTION_COUNT
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import ClientClosedError
from mootdx_next.errors import TransportError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.ex_markets import ExMarket
from mootdx_next.ex_markets import ExMarketRegistry
from mootdx_next.ex_markets import ex_market_registry as default_ex_market_registry
from mootdx_next.interfaces import AbstractProtocol
from mootdx_next.interfaces import AbstractScheduler
from mootdx_next.interfaces import AbstractTransport
from mootdx_next.mac import MacAdjust
from mootdx_next.mac import MacFieldSelection
from mootdx_next.mac import MacPeriod
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.params import normalize_date
from mootdx_next.params import normalize_frequency
from mootdx_next.protocol import ExQuoteProtocol
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool
from mootdx_next.transport.constants import EX_SETUP_PAYLOADS
from mootdx_next.transport.constants import DEFAULT_HEARTBEAT_INTERVAL_SEC
from mootdx_next.transport.socket_transport import SyncSocketTransport
from mootdx_next.api.mac_clients import MacClientMixin

MAC_EX_HK_TRANSACTION_MARKETS = frozenset({27, 31, 48, 49, 71, 98})


def _default_ex_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in EX_HOSTS]


def _ex_transport_factory() -> SyncSocketTransport:
    return SyncSocketTransport(setup_payloads=EX_SETUP_PAYLOADS)


EX_REQUEST_APIS = frozenset(
    {
        "markets",
        "instrument_count",
        "instrument",
        "instruments",
        "quote",
        "quotes",
        "bars",
        "minute",
        "minutes",
        "transaction",
        "transactions",
        "bars_range",
        "mac_quotes",
        "mac_quotes_list",
        "mac_bars",
        "mac_tick_chart",
        "mac_chart_sampling",
        "mac_transactions",
    }
)


class ExSyncClient(MacClientMixin):
    """Native synchronous client for TDX extended markets (ExHq)."""

    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        heartbeat: bool = False,
        market_registry: ExMarketRegistry | None = None,
        mac_transport: AbstractTransport | None = None,
        mac_protocol: AbstractProtocol | None = None,
        mac_scheduler: AbstractScheduler | None = None,
        mac_connection_pool: ConnectionPool | None = None,
        mac_servers: list[ServerEndpoint] | None = None,
    ) -> None:
        self.transport = transport
        self.protocol = protocol or ExQuoteProtocol()
        self.max_retries = max_retries
        if int(timeout_ms) <= 0:
            raise ValueError("timeout_ms must be greater than zero")
        self.timeout_ms = int(timeout_ms)
        self.heartbeat = bool(heartbeat)
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.market_registry = market_registry or default_ex_market_registry
        self._init_mac(
            mac_transport=mac_transport,
            mac_protocol=mac_protocol,
            mac_scheduler=mac_scheduler,
            mac_connection_pool=mac_connection_pool,
            mac_servers=mac_servers,
            mac_capability="mac_ex",
            mac_ex=True,
        )
        self._closed = False
        self.connection_pool = connection_pool or ConnectionPool(
            transport_factory=transport.__class__ if transport is not None else _ex_transport_factory
        )
        self.scheduler = scheduler or ServerPool(
            servers=servers or _default_ex_servers(),
            connection_pool=self.connection_pool,
        )
        if self.heartbeat:
            self._start_heartbeat()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._stop_heartbeat()
        self.connection_pool.close_all()
        if getattr(self, "_mac_connection_pool", None) is not None:
            self._mac_connection_pool.close_all()
        self._closed = True

    def reconnect(self) -> None:
        self.connection_pool.close_all()
        if getattr(self, "_mac_connection_pool", None) is not None:
            self._mac_connection_pool.close_all()
        self._closed = False
        if self.heartbeat:
            self._start_heartbeat()

    def request(self, api: str, **kwargs: Any) -> object:
        if api not in EX_REQUEST_APIS:
            raise NotImplementedError(f"ExSyncClient.request() does not support api: {api}")
        if api.startswith("mac_"):
            return getattr(self, api)(**kwargs)
        return getattr(self, api)(**kwargs)

    def mac_quotes(
        self,
        stocks: Any,
        *,
        fields: MacFieldSelection | None = None,
    ) -> list[dict[str, object]]:
        return MacClientMixin.mac_quotes(self, stocks, fields=fields)

    def mac_quotes_list(
        self,
        market: int,
        *,
        start: int = 0,
        count: int = 80,
        fields: MacFieldSelection | None = None,
    ) -> list[dict[str, object]]:
        instruments = self._instrument_market_slice(
            int(market), start=int(start), count=int(count)
        )
        symbols = [
            (int(market), str(row.get("code", "")).strip())
            for row in instruments
            if str(row.get("code", "")).strip()
        ]
        rows: list[dict[str, object]] = []
        for offset in range(0, len(symbols), 80):
            rows.extend(self.mac_quotes(symbols[offset : offset + 80], fields=fields))
        return rows

    def mac_bars(
        self,
        market: int,
        symbol: str,
        frequency: MacPeriod = MacPeriod.DAY,
        *,
        start: int = 0,
        count: int = 700,
        adjust: MacAdjust = MacAdjust.NONE,
    ) -> list[dict[str, object]]:
        return MacClientMixin.mac_bars(
            self,
            (int(market), str(symbol)),
            frequency,
            start=start,
            count=count,
            adjust=adjust,
        )

    def mac_tick_chart(
        self,
        market: int,
        symbol: str,
        *,
        date: object | None = None,
    ) -> list[dict[str, object]]:
        return MacClientMixin.mac_tick_chart(self, (int(market), str(symbol)), date=date)

    def mac_chart_sampling(self, market: int, symbol: str) -> list[dict[str, object]]:
        return MacClientMixin.mac_chart_sampling(self, (int(market), str(symbol)))

    def mac_transactions(
        self,
        market: int,
        symbol: str,
        *,
        date: object | None = None,
        start: int = 0,
        count: int = 2000,
    ) -> list[dict[str, object]]:
        # 0x122F is not wired to the Hong Kong stock data source. Preserve the
        # established ExHq 0x23FC/0x2406 compatibility route and normalize it
        # into the MAC transaction schema.
        if int(market) in MAC_EX_HK_TRANSACTION_MARKETS:
            if start < 0:
                raise ValueError("start must be greater than or equal to zero")
            if count <= 0:
                raise ValueError("count must be greater than zero")
            result: list[dict[str, object]] = []
            offset = start
            while len(result) < count:
                page_size = min(count - len(result), MAX_EX_TRANSACTION_COUNT)
                page = (
                    self.transaction(
                        int(market), str(symbol), start=offset, offset=page_size
                    )
                    if date is None
                    else self.transactions(
                        int(market), str(symbol), date, start=offset, offset=page_size
                    )
                )
                if not page:
                    break
                result.extend(
                    {
                        "time": row.get("time"),
                        "price": row.get("price"),
                        "vol": row.get("volume", 0),
                        "trade_count": 0,
                        "bs_flag": row.get("nature", 0),
                    }
                    for row in page
                )
                offset += len(page)
                if len(page) < page_size:
                    break
            return result
        return MacClientMixin.mac_transactions(
            self,
            (int(market), str(symbol)),
            date=date,
            start=start,
            count=count,
        )

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

    def _instrument_market_slice(
        self,
        market: int,
        *,
        start: int,
        count: int,
    ) -> list[dict[str, object]]:
        """Return a market-local slice from the standard Ex instrument directory."""

        if start < 0:
            raise ValueError("start must be greater than or equal to zero")
        if count <= 0:
            raise ValueError("count must be greater than zero")
        total = self.instrument_count()
        if total <= 0:
            return []

        # The upstream Ex directory is ordered by market. Locate the first
        # candidate without downloading unrelated markets.
        low, high = 0, total
        while low < high:
            middle = (low + high) // 2
            probe = self.instrument(start=middle, offset=1)
            if not probe:
                high = middle
                continue
            probe_market = int(probe[0].get("market", -1))
            if probe_market < market:
                low = middle + 1
            else:
                high = middle

        rows: list[dict[str, object]] = []
        skipped = 0
        position = low
        while position < total and len(rows) < count:
            requested = min(MAX_EX_INSTRUMENT_COUNT, total - position)
            page = self.instrument(start=position, offset=requested)
            if not page:
                break
            reached_later_market = False
            for item in page:
                item_market = int(item.get("market", -1))
                if item_market < market:
                    continue
                if item_market > market:
                    reached_later_market = True
                    break
                if skipped < start:
                    skipped += 1
                    continue
                rows.append(dict(item))
                if len(rows) >= count:
                    break
            if len(rows) >= count or reached_later_market or len(page) < requested:
                break
            position += len(page)
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
        if api.startswith("mac_"):
            return self.request(api, **kwargs)
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
            raise ClientClosedError("client is closed; call reconnect() before sending requests")

        if context.timeout_ms == DEFAULT_REQUEST_TIMEOUT_MS:
            context.timeout_ms = self.timeout_ms

        excluded: set[tuple[str, int]] = set()
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            server = self.scheduler.select_server(context, excluded=excluded)
            server_key = (server.host, server.port)
            try:
                lease = self.connection_pool.acquire(server)
            except PoolExhaustedError:
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

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="mootdx-next-ex-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        self._heartbeat_thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(DEFAULT_HEARTBEAT_INTERVAL_SEC):
            if self._closed:
                continue
            try:
                self.instrument_count()
            except ClientClosedError:
                return
            except Exception:
                continue


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
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        heartbeat: bool = False,
        sync_client: ExSyncClient | None = None,
        market_registry: ExMarketRegistry | None = None,
        mac_transport: AbstractTransport | None = None,
        mac_protocol: AbstractProtocol | None = None,
        mac_scheduler: AbstractScheduler | None = None,
        mac_connection_pool: ConnectionPool | None = None,
        mac_servers: list[ServerEndpoint] | None = None,
    ) -> None:
        self._explicit_sync_client = sync_client
        self._thread_local = threading.local()
        self._clients_lock = threading.Lock()
        self._worker_clients: list[ExSyncClient] = []
        self._closed = False
        self._heartbeat = bool(heartbeat)
        self._sync_client_kwargs = {
            "transport": transport,
            "protocol": protocol,
            "scheduler": scheduler,
            "connection_pool": connection_pool,
            "servers": servers,
            "max_retries": max_retries,
            "timeout_ms": timeout_ms,
            "heartbeat": False,
            "market_registry": market_registry,
            "mac_transport": mac_transport,
            "mac_protocol": mac_protocol,
            "mac_scheduler": mac_scheduler,
            "mac_connection_pool": mac_connection_pool,
            "mac_servers": mac_servers,
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

    async def mac_quotes(self, stocks: Any, **kwargs: Any) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_quotes", stocks, **kwargs))

    async def mac_quotes_list(self, market: int, **kwargs: Any) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_quotes_list", market=market, **kwargs))

    async def mac_bars(
        self,
        market: int,
        symbol: str,
        frequency: MacPeriod = MacPeriod.DAY,
        **kwargs: Any,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_bars", market, symbol, frequency, **kwargs))

    async def mac_tick_chart(self, market: int, symbol: str, **kwargs: Any) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_tick_chart", market, symbol, **kwargs))

    async def mac_chart_sampling(self, market: int, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_chart_sampling", market, symbol))

    async def mac_transactions(self, market: int, symbol: str, **kwargs: Any) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "mac_transactions", market, symbol, **kwargs))

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
            with self._clients_lock:
                if self._closed:
                    raise ClientClosedError("client is closed; call reconnect() before sending requests")
                client_options = dict(self._sync_client_kwargs)
                client_options["heartbeat"] = self._heartbeat and not self._worker_clients
                client = ExSyncClient(**client_options)
                self._thread_local.client = client
                self._worker_clients.append(client)
                self._closed = False
        return client


# Keep only the MAC EX methods that have an explicit extended-market signature
# on ExSyncClient. A-share-only helpers remain on SyncClient and are not
# advertised as extended-market APIs.
for _mac_method_name in (
    "mac_quotes",
    "mac_quotes_list",
    "mac_bars",
    "mac_tick_chart",
    "mac_chart_sampling",
    "mac_transactions",
):
    if _mac_method_name not in ExSyncClient.__dict__:
        setattr(ExSyncClient, _mac_method_name, getattr(MacClientMixin, _mac_method_name))


__all__ = ["AsyncExClient", "ExSyncClient"]
