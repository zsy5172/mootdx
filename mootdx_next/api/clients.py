from __future__ import annotations

import asyncio
import struct
import threading
from collections.abc import Mapping
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
DEFAULT_MAX_REPORT_FILE_SIZE = 256 * 1024 * 1024
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
        config_registry: ZhbRegistry | None = None,
        bse_registry: BseRegistry | None = None,
        bse_provider: BseProvider | None = None,
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

        if market == 2:
            return len(self.bse_registry.get())

        context = RequestContext(api="stock_count", params={"market": market})
        payload = self.protocol.encode("stock_count", market=market)
        envelope = self._send(context, payload)
        return int(self.protocol.decode("stock_count", envelope))

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
            context = RequestContext(api="stock_list_page", params={"market": market, "start": start})
            payload = self.protocol.encode("stock_list_page", market=market, start=start)
            envelope = self._send(context, payload)
            page = self.protocol.decode("stock_list_page", envelope)
            for row in page:
                row["market"] = market
                row["source"] = "tdx"
            rows.extend(page)

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
        if market not in {0, 1, 2}:
            raise UnsupportedMarketError("unsupported market for minutes: only sh/sz/bj are supported")

        normalized_date = normalize_date(date)
        code = normalize_symbol(normalized_symbol)
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
            )
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
            )
        )

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
        config_registry: ZhbRegistry | None = None,
        bse_registry: BseRegistry | None = None,
        bse_provider: BseProvider | None = None,
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

    async def stocks(self, market: int, refresh: bool = False) -> list[dict[str, object]]:
        if refresh:
            return list(await asyncio.to_thread(self._call_sync, "stocks", market, refresh=True))
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

    async def call_auction(self, symbol: str) -> list[dict[str, object]]:
        return list(await asyncio.to_thread(self._call_sync, "call_auction", symbol))

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
