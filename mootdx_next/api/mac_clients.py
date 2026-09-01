"""MAC 高层请求 mixin。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mootdx_next.constants import CAPABILITY_MAC_A
from mootdx_next.errors import (
    ClientClosedError,
    PoolExhaustedError,
    ProtocolDecodeError,
    TransportError,
)
from mootdx_next.mac.types import (
    MacAdjust,
    MacBoardSortColumn,
    MacBoardType,
    MacCategory,
    MacFieldSelection,
    MacFilter,
    MacPeriod,
    MacSortOrder,
    MacSortType,
)
from mootdx_next.models import RequestContext, ServerEndpoint
from mootdx_next.protocol.mac import MacExProtocol, MacProtocol


def mac_board_code(value: str) -> int:
    raw = str(value).strip()
    if raw.startswith("US"):
        return 30000 + int(raw[2:])
    if raw.startswith("HK"):
        return 20000 + int(raw[2:])
    if len(raw) == 6 and raw.isdigit():
        number = int(raw)
        if raw.startswith("88"):
            return number - 880000 + 20000
        if raw.startswith("399"):
            return number - 399000 + 30000
        if raw.startswith("899"):
            return number - 899000 + 32000
        if raw.startswith("000"):
            return 31000 + number
    return int(raw)


class MacClientMixin:
    """供 SyncClient/ExSyncClient 复用的 MAC 方法。"""

    def _init_mac(
        self,
        *,
        mac_transport: Any = None,
        mac_protocol: Any = None,
        mac_scheduler: Any = None,
        mac_connection_pool: Any = None,
        mac_servers: list[ServerEndpoint] | None = None,
        mac_capability: str = CAPABILITY_MAC_A,
        mac_ex: bool = False,
    ) -> None:
        self._mac_transport = mac_transport
        self._mac_protocol = mac_protocol or (MacExProtocol() if mac_ex else MacProtocol())
        self._mac_scheduler = mac_scheduler
        self._mac_connection_pool = mac_connection_pool
        self._mac_servers = [
            ServerEndpoint(
                host=item.host,
                port=item.port,
                label=item.label,
                market=item.market,
                capabilities=frozenset(set(item.capabilities) | {mac_capability}),
            )
            for item in mac_servers
        ] if mac_servers is not None else None
        self._mac_capability = mac_capability
        self._mac_ex = mac_ex
        self._mac_max_retries = getattr(self, "max_retries", 1)
        self._mac_timeout_ms = getattr(self, "timeout_ms", 15000)

    def _mac_request(self, api: str, **kwargs: Any) -> object:
        if getattr(self, "_closed", False):
            raise ClientClosedError("client is closed; call reconnect() before sending requests")
        context = RequestContext(
            api=api,
            params=dict(kwargs),
            required_capabilities=frozenset({self._mac_capability}),
        )
        if context.timeout_ms == 15000:
            context.timeout_ms = self._mac_timeout_ms
        kwargs.setdefault("extended", self._mac_ex)
        payload = self._mac_protocol.encode(api, **kwargs)
        envelope = self._mac_send(context, payload)
        return self._mac_protocol.decode(api, envelope, **kwargs)

    def _mac_send(self, context: RequestContext, payload: bytes):
        if self._mac_scheduler is None:
            self._create_mac_runtime()
        excluded: set[tuple[str, int]] = set()
        for attempt in range(self._mac_max_retries + 1):
            server = self._mac_scheduler.select_server(context, excluded=excluded)
            key = (server.host, server.port)
            try:
                lease = self._mac_connection_pool.acquire(server)
            except PoolExhaustedError:
                excluded.add(key)
                if attempt < self._mac_max_retries:
                    continue
                raise
            try:
                envelope = lease.transport.send(context, payload, server)
                self._mac_scheduler.record_success(server, lease.transport.metrics)
                self._mac_connection_pool.release(lease)
                return envelope
            except Exception as exc:
                self._mac_scheduler.record_failure(server, exc)
                self._mac_connection_pool.discard(lease)
                excluded.add(key)
                if isinstance(exc, (TransportError, ProtocolDecodeError)) and attempt < self._mac_max_retries:
                    continue
                raise
        raise RuntimeError("unreachable")

    def _create_mac_runtime(self) -> None:
        from mootdx_next.constants import MAC_EX_HOSTS, MAC_HOSTS
        from mootdx_next.scheduler.pools import ConnectionPool, ServerPool
        from mootdx_next.transport.mac import MacExSocketTransport, MacSocketTransport

        transport_type = MacExSocketTransport if self._mac_ex else MacSocketTransport
        if self._mac_transport is None:
            self._mac_transport = transport_type
        supplied_instance = self._mac_transport is not None and not isinstance(self._mac_transport, type)
        factory = self._mac_transport if isinstance(self._mac_transport, type) else self._mac_transport.__class__
        if supplied_instance and self._mac_connection_pool is None:
            # An injected transport instance is a single-resource adapter
            # (normally a test double).  Keep the default pool from leasing it
            # concurrently; callers needing multiple transports can inject a
            # ConnectionPool with their own factory.
            supplied = self._mac_transport

            def factory():
                return supplied
        self._mac_connection_pool = self._mac_connection_pool or ConnectionPool(
            transport_factory=factory,
            max_connections_per_server=1 if supplied_instance else 2,
        )
        self._mac_scheduler = self._mac_scheduler or ServerPool(
            servers=self._mac_servers or [
                ServerEndpoint(host=host, port=port, label=label, capabilities=frozenset({self._mac_capability}))
                for label, host, port in (MAC_EX_HOSTS if self._mac_ex else MAC_HOSTS)
            ], connection_pool=self._mac_connection_pool
        )

    def mac_quotes(self, symbols: Any, *, fields: MacFieldSelection | None = None) -> list[dict[str, object]]:
        values = [symbols] if isinstance(symbols, str) or (isinstance(symbols, tuple) and len(symbols) == 2 and isinstance(symbols[0], int)) else list(symbols)
        return list(self._mac_request("mac_ex_quotes" if self._mac_ex else "mac_quotes", symbols=values, fields=fields))

    @staticmethod
    def _collect_mac_pages(
        fetch: Any,
        *,
        start: int,
        count: int,
        page_size: int,
        prepend: bool = False,
    ) -> list[dict[str, object]]:
        if start < 0:
            raise ValueError("start must be greater than or equal to zero")
        if count <= 0:
            raise ValueError("count must be greater than zero")

        rows: list[dict[str, object]] = []
        fetched = 0
        offset = start
        previous_page: list[dict[str, object]] | None = None
        while fetched < count:
            requested = min(count - fetched, page_size)
            page = [dict(row) for row in fetch(offset, requested)]
            if not page:
                break
            if page == previous_page:
                raise ProtocolDecodeError("MAC pagination did not advance")
            rows = page + rows if prepend else rows + page
            fetched += len(page)
            offset += len(page)
            if len(page) < requested:
                break
            previous_page = page
        return rows

    def mac_quotes_list(self, category: int = MacCategory.A, *, market: int | None = None, start: int = 0, count: int = 80, sort_by: MacSortType = MacSortType.CHANGE_PCT, sort_order: MacSortOrder = MacSortOrder.DESC, exclude_flags: Iterable[MacFilter] | None = None, fields: MacFieldSelection | None = None) -> list[dict[str, object]]:
        if self._mac_ex:
            raise NotImplementedError(
                "extended MAC quote lists must be composed from the Ex instrument directory"
            )
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_quotes_list",
                board_code=int(category),
                start=offset,
                count=size,
                sort_by=sort_by,
                sort_order=sort_order,
                exclude_flags=exclude_flags,
                fields=fields,
            ),
            start=start,
            count=count,
            page_size=80,
        )

    def mac_board_list(
        self,
        board_type: MacBoardType = MacBoardType.ALL,
        *,
        start: int = 0,
        count: int = 150,
        sort_column: MacBoardSortColumn = MacBoardSortColumn.CHANGE_PCT,
    ) -> list[dict[str, object]]:
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_board_list",
                board_type=board_type,
                start=offset,
                count=size,
                sort_column=sort_column,
            ),
            start=start,
            count=count,
            page_size=150,
        )

    def mac_board_members(self, block: str, *, start: int = 0, count: int = 80, sort_by: MacSortType = MacSortType.CHANGE_PCT, sort_order: MacSortOrder = MacSortOrder.DESC, exclude_flags: Iterable[MacFilter] | None = None, fields: MacFieldSelection | None = None) -> list[dict[str, object]]:
        board_code = mac_board_code(block)
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_board_members",
                board_code=board_code,
                start=offset,
                count=size,
                sort_by=sort_by,
                sort_order=sort_order,
                exclude_flags=exclude_flags,
                fields=fields,
            ),
            start=start,
            count=count,
            page_size=80,
        )

    def mac_belong_board(self, symbol: Any) -> list[dict[str, object]]:
        return list(self._mac_request("mac_belong_board", symbol=symbol))

    def mac_capital_flow(self, symbol: Any) -> list[dict[str, object]]:
        return list(self._mac_request("mac_capital_flow", symbol=symbol))

    def mac_symbol_info(self, symbol: Any) -> list[dict[str, object]]:
        return list(self._mac_request("mac_symbol_info", symbol=symbol))

    def mac_bars(self, symbol: Any, frequency: MacPeriod = MacPeriod.DAY, *, start: int = 0, count: int = 700, times: int = 1, adjust: MacAdjust = MacAdjust.NONE) -> list[dict[str, object]]:
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_bars",
                symbol=symbol,
                frequency=frequency,
                start=offset,
                count=size,
                times=times,
                adjust=adjust,
            ),
            start=start,
            count=count,
            page_size=700,
            prepend=True,
        )

    def mac_tick_chart(self, symbol: Any, *, date: object | None = None) -> list[dict[str, object]]:
        return list(self._mac_request("mac_tick_chart", symbol=symbol, date=date))

    def mac_tick_charts(self, symbol: Any, *, date: object | None = None, days: int = 5) -> list[dict[str, object]]:
        if days < 1 or days > 5:
            raise ValueError("days must be between 1 and 5")
        return list(self._mac_request("mac_tick_charts", symbol=symbol, date=date, days=days))

    def mac_chart_sampling(self, symbol: Any) -> list[dict[str, object]]:
        return list(self._mac_request("mac_chart_sampling", symbol=symbol))

    def mac_transactions(self, symbol: Any, *, date: object | None = None, start: int = 0, count: int = 2000) -> list[dict[str, object]]:
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_transactions", symbol=symbol, date=date, start=offset, count=size
            ),
            start=start,
            count=count,
            page_size=1000,
        )

    def mac_auction(self, symbol: Any, *, start: int = 0, count: int = 500) -> list[dict[str, object]]:
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_auction", symbol=symbol, start=offset, count=size
            ),
            start=start,
            count=count,
            page_size=500,
        )

    def mac_unusual(self, market: int, *, start: int = 0, count: int = 600) -> list[dict[str, object]]:
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_unusual", market=market, start=offset, count=size
            ),
            start=start,
            count=count,
            page_size=600,
        )

    def mac_server_info(self) -> list[dict[str, object]]:
        return list(self._mac_request("mac_server_info"))

    def mac_kline_offset(self, *, offset: int = 0, count: int = 128000) -> list[dict[str, object]]:
        return list(self._mac_request("mac_kline_offset", offset=offset, count=count))

    def mac_file_meta(self, filename: str, *, offset: int = 0) -> dict[str, object]:
        return dict(self._mac_request("mac_file_meta", filename=filename, offset=offset))

    def mac_file_chunk(self, filename: str, *, index: int = 1, offset: int = 0, size: int = 30000) -> bytes:
        return bytes(self._mac_request("mac_file_chunk", filename=filename, index=index, offset=offset, size=size))

    def mac_file(self, filename: str, *, filesize: int = 0) -> bytes:
        if filesize <= 0:
            filesize = int(self.mac_file_meta(filename).get("size", 0))
        data = bytearray()
        offset = 0
        index = 1
        while offset < filesize:
            chunk = self.mac_file_chunk(filename, index=index, offset=offset, size=min(30000, filesize - offset))
            if not chunk:
                break
            data.extend(chunk)
            offset += len(chunk)
            index += 1
            if len(chunk) < 30000:
                break
        return bytes(data)

    def mac_goods_list(self, market: int, *, start: int = 0, count: int = 600) -> list[dict[str, object]]:
        if self._mac_ex:
            raise NotImplementedError(
                "mac_goods_list is an A-share MAC command; use the Ex instrument directory"
            )
        return self._collect_mac_pages(
            lambda offset, size: self._mac_request(
                "mac_goods_list", market=market, start=offset, count=size
            ),
            start=start,
            count=count,
            page_size=1000,
        )


MAC_METHOD_NAMES = (
    "mac_quotes",
    "mac_quotes_list",
    "mac_board_list",
    "mac_board_members",
    "mac_belong_board",
    "mac_capital_flow",
    "mac_symbol_info",
    "mac_bars",
    "mac_tick_chart",
    "mac_tick_charts",
    "mac_chart_sampling",
    "mac_transactions",
    "mac_auction",
    "mac_unusual",
    "mac_server_info",
    "mac_kline_offset",
    "mac_file_meta",
    "mac_file_chunk",
    "mac_file",
    "mac_goods_list",
)


__all__ = ["MAC_METHOD_NAMES", "MacClientMixin", "mac_board_code"]
