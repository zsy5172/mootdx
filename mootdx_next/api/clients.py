from __future__ import annotations

import asyncio
import math
import struct
import threading
from typing import Any

from mootdx_next.constants import HQ_HOSTS
from mootdx_next.constants import MAX_HISTORY_TRANSACTION_COUNT
from mootdx_next.constants import MAX_LIMIT_PRICE_COUNT
from mootdx_next.constants import MAX_TRANSACTION_COUNT
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.errors import PoolExhaustedError
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
from mootdx_next.params import normalize_date
from mootdx_next.params import normalize_frequency
from mootdx_next.params import today_yyyymmdd
from mootdx_next.protocol import StdQuoteProtocol
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool
from mootdx_next.symbols import get_stock_market
from mootdx_next.symbols import get_stock_markets
from mootdx_next.symbols import normalize_symbol
from mootdx_next.symbols import normalize_symbol_input
from mootdx_next.transport.socket_transport import SyncSocketTransport

BLOCK_CHUNK_SIZE = 0x7530
TRANSACTION_MAX_OFFSET = MAX_TRANSACTION_COUNT
HISTORY_TRANSACTION_MAX_OFFSET = MAX_HISTORY_TRANSACTION_COUNT
LIMIT_PRICE_MAX_OFFSET = MAX_LIMIT_PRICE_COUNT


def _default_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in HQ_HOSTS]


class SyncClient:
    def __init__(
        self,
        transport: AbstractTransport | None = None,
        protocol: AbstractProtocol | None = None,
        scheduler: AbstractScheduler | None = None,
        connection_pool: ConnectionPool | None = None,
        servers: list[ServerEndpoint] | None = None,
        max_retries: int = 1,
    ) -> None:
        self.transport = transport
        self.protocol = protocol or StdQuoteProtocol()
        self.max_retries = max_retries
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
        raise NotImplementedError("SyncClient.request() is not implemented in Week 1")

    def stock_count(self, market: int) -> int:
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError(f"unsupported market for stock_count: {market}")

        context = RequestContext(api="stock_count", params={"market": market})
        payload = self.protocol.encode("stock_count", market=market)
        envelope = self._send(context, payload)
        return int(self.protocol.decode("stock_count", envelope))

    def stocks(self, market: int) -> list[dict[str, object]]:
        if market not in {0, 1}:
            raise UnsupportedMarketError(f"unsupported market for stocks: {market}")

        count = self.stock_count(market)
        if count <= 0:
            return []

        rows: list[dict[str, object]] = []
        for start in range(0, count, 1000):
            context = RequestContext(api="stock_list_page", params={"market": market, "start": start})
            payload = self.protocol.encode("stock_list_page", market=market, start=start)
            envelope = self._send(context, payload)
            rows.extend(self.protocol.decode("stock_list_page", envelope))

        return rows

    def quotes(self, symbol: str | list[str] | None = None) -> list[dict[str, object]]:
        normalized = normalize_symbol_input(symbol)
        if not normalized:
            return []

        symbols = get_stock_markets(normalized)
        context = RequestContext(api="quotes", params={"symbol": normalized})
        payload = self.protocol.encode("quotes", symbols=symbols)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("quotes", envelope))

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
        if start < 0:
            raise ValueError("start must be >= 0")
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
        if start < 0:
            raise ValueError("start must be >= 0")
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

    def minutes(self, symbol: str, date: str | int) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for minutes: only sh/sz are supported")

        normalized_date = normalize_date(date)
        code = normalize_symbol(normalized_symbol)
        context = RequestContext(
            api="minutes",
            params={"symbol": normalized_symbol, "market": market, "date": normalized_date},
        )
        payload = self.protocol.encode("minutes", market=market, code=code, date=normalized_date)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("minutes", envelope, market=market, code=code))

    def minute(self, symbol: str) -> list[dict[str, object]]:
        return self.minutes(symbol=symbol, date=today_yyyymmdd())

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
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for transaction: only sh/sz are supported")

        code = normalize_symbol(normalized_symbol)
        context = RequestContext(
            api="transaction",
            params={"symbol": normalized_symbol, "market": market, "start": start, "offset": offset},
        )
        payload = self.protocol.encode("transaction", market=market, code=code, start=start, count=offset)
        envelope = self._send(context, payload)
        return list(self.protocol.decode("transaction", envelope))

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
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for transactions: only sh/sz are supported")

        normalized_date = normalize_date(date)
        code = normalize_symbol(normalized_symbol)
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
        return list(self.protocol.decode("transactions", envelope))

    def finance(self, symbol: str) -> dict[str, object]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for finance: only sh/sz are supported")

        code = normalize_symbol(normalized_symbol)
        context = RequestContext(api="finance", params={"symbol": normalized_symbol, "market": market})
        payload = self.protocol.encode("finance", market=market, code=code)
        envelope = self._send(context, payload)
        return dict(self.protocol.decode("finance", envelope))

    def block(self, block_file: str = "block.dat") -> list[dict[str, object]]:
        context = RequestContext(api="block_info_meta", params={"block_file": block_file})
        meta_payload = self.protocol.encode("block_info_meta", block_file=block_file)
        meta_envelope = self._send(context, meta_payload)
        meta = self.protocol.decode("block_info_meta", meta_envelope)
        size = int(meta["size"])

        if size <= 0:
            return []

        content = bytearray()
        chunks = math.ceil(size / BLOCK_CHUNK_SIZE)
        for seg in range(chunks):
            start = seg * BLOCK_CHUNK_SIZE
            piece_context = RequestContext(api="block_info", params={"block_file": block_file, "start": start, "size": size})
            payload = self.protocol.encode("block_info", block_file=block_file, start=start, size=size)
            envelope = self._send(piece_context, payload)
            content.extend(self.protocol.decode("block_info", envelope))

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

    def f10_categories(self, symbol: str) -> list[dict[str, object]]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidSymbolError("symbol cannot be blank")

        normalized_symbol = symbol.strip()
        market = int(get_stock_market(normalized_symbol, string=False))
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for f10: only sh/sz are supported")

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
        if market not in {0, 1}:
            raise UnsupportedMarketError("unsupported market for f10: only sh/sz are supported")

        code = normalize_symbol(normalized_symbol)
        categories = self.f10_categories(normalized_symbol)
        matched = next((item for item in categories if item["name"] == normalized_name), None)
        if matched is None:
            raise UnknownF10CategoryError(f"unknown f10 category: {normalized_name}")

        context = RequestContext(
            api="f10_content",
            params={
                "symbol": normalized_symbol,
                "market": market,
                "name": normalized_name,
                "filename": matched["filename"],
                "start": matched["start"],
                "length": matched["length"],
            },
        )
        payload = self.protocol.encode(
            "f10_content",
            market=market,
            code=code,
            filename=matched["filename"],
            start=int(matched["start"]),
            length=int(matched["length"]),
        )
        envelope = self._send(context, payload)
        return str(self.protocol.decode("f10_content", envelope))

    def _all_limit_prices(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for start in range(0, 0x10000, LIMIT_PRICE_MAX_OFFSET):
            count = min(LIMIT_PRICE_MAX_OFFSET, 0x10000 - start)
            page = self.limit_prices(start=start, count=count)
            rows.extend(page)
            if len(page) < count:
                break
        return rows

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
        return await asyncio.to_thread(self._call_sync, api, **kwargs)

    async def stock_count(self, market: int) -> int:
        return int(await asyncio.to_thread(self._call_sync, "stock_count", market))

    async def stocks(self, market: int) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "stocks", market))

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

    async def minutes(self, symbol: str, date: str | int) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "minutes", symbol, date))

    async def minute(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "minute", symbol))

    async def transaction(self, symbol: str, start: int = 0, offset: int = 800) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "transaction", symbol, start, offset))

    async def transactions(
        self,
        symbol: str,
        date: str | int,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "transactions", symbol, date, start, offset))

    async def finance(self, symbol: str) -> dict[str, object]:
        return dict(await asyncio.to_thread(self._call_sync, "finance", symbol))

    async def xdxr(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "xdxr", symbol))

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

    async def block(self, block_file: str = "block.dat") -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "block", block_file))

    async def f10_categories(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "f10_categories", symbol))

    async def f10_content(self, symbol: str, name: str) -> str:
        return str(await asyncio.to_thread(self._call_sync, "f10_content", symbol, name))

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
    if market is not None:
        return int(market)
    return 1 if symbol[:2] in ["00", "88", "99"] else 0


def _parse_block_content(data: bytes | bytearray) -> list[dict[str, object]]:
    if len(data) < 386:
        return []

    pos = 384
    try:
        (num,) = struct.unpack("<H", data[pos : pos + 2])
    except struct.error:
        return []

    pos += 2
    rows: list[dict[str, object]] = []

    for _ in range(num):
        block_name_raw = data[pos : pos + 9]
        pos += 9
        stock_count, block_type = struct.unpack("<HH", data[pos : pos + 4])
        pos += 4
        block_stock_begin = pos

        for code_index in range(stock_count):
            code = data[pos : pos + 7].decode("utf-8", "ignore").rstrip("\x00")
            pos += 7
            rows.append(
                {
                    "blockname": block_name_raw.decode("gbk", "ignore").rstrip("\x00"),
                    "block_type": block_type,
                    "code_index": code_index,
                    "code": code,
                }
            )

        pos = block_stock_begin + 2800

    return rows
