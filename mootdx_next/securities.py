from __future__ import annotations

import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mootdx_next.constants import MARKET_BJ
from mootdx_next.constants import MARKET_SH
from mootdx_next.constants import MARKET_SZ
from mootdx_next.symbols import is_etf
from mootdx_next.symbols import is_index
from mootdx_next.symbols import is_stock

SECURITY_CACHE_TTL_SECONDS = 10 * 60
SECURITY_MARKETS = (MARKET_SH, MARKET_SZ, MARKET_BJ)
MARKET_PREFIXES = {
    MARKET_SZ: "sz",
    MARKET_SH: "sh",
    MARKET_BJ: "bj",
}


@dataclass(frozen=True, slots=True)
class Security:
    """Immutable standard-market directory metadata."""

    market: int
    code: str
    name: str
    security_type: str
    volunit: int | None = None
    decimal_point: int | None = None
    pre_close: float | None = None
    source: str = "tdx"

    @property
    def symbol(self) -> str:
        return f"{MARKET_PREFIXES[self.market]}{self.code}"

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "code": self.code,
            "symbol": self.symbol,
            "name": self.name,
            "security_type": self.security_type,
            "volunit": self.volunit,
            "decimal_point": self.decimal_point,
            "pre_close": self.pre_close,
            "source": self.source,
        }


SecuritySnapshot = tuple[Security, ...]
SecurityLoader = Callable[[], Iterable[Security]]


class SecurityRegistry:
    """Process-level, thread-safe cache of an immutable security directory."""

    def __init__(
        self,
        *,
        ttl_seconds: float = SECURITY_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: SecuritySnapshot = ()
        self._by_key: Mapping[tuple[int, str], Security] = MappingProxyType({})
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self, loader: SecurityLoader, *, refresh: bool = False) -> SecuritySnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._loaded:
                    return self._snapshot
            if not refresh and self._loaded and self._time_fn() < self._expires_at:
                return self._snapshot
            self._refreshing = True

        try:
            snapshot = self._normalize(loader())
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._snapshot = snapshot
            self._by_key = MappingProxyType(
                {(item.market, item.code): item for item in snapshot}
            )
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return snapshot

    def refresh(self, loader: SecurityLoader) -> SecuritySnapshot:
        return self.get(loader, refresh=True)

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    def snapshot(self) -> SecuritySnapshot:
        with self._condition:
            return self._snapshot

    def find(self, market: int, code: str) -> Security | None:
        """Return metadata from the fresh immutable snapshot without loading it."""

        with self._condition:
            if not self._loaded or self._time_fn() >= self._expires_at:
                return None
            return self._by_key.get((int(market), str(code)))

    @staticmethod
    def _normalize(values: Iterable[Security]) -> SecuritySnapshot:
        snapshot = tuple(values)
        seen: set[tuple[int, str]] = set()
        for item in snapshot:
            if not isinstance(item, Security):
                raise TypeError("security loader must return Security instances")
            if item.market not in SECURITY_MARKETS:
                raise ValueError(f"invalid security market: {item.market}")
            if len(item.code) != 6 or not item.code.isdigit():
                raise ValueError(f"invalid security code: {item.code}")
            if item.security_type not in {"stock", "etf", "index", "other"}:
                raise ValueError(f"invalid security type: {item.security_type}")
            key = (item.market, item.code)
            if key in seen:
                raise ValueError(f"duplicate security: {item.symbol}")
            seen.add(key)
        return snapshot


def classify_security(market: int, code: str) -> str:
    symbol = f"{MARKET_PREFIXES[int(market)]}{code}"
    if is_stock(symbol, market):
        return "stock"
    if is_etf(symbol, market):
        return "etf"
    if is_index(symbol, market):
        return "index"
    return "other"


security_registry = SecurityRegistry()


def security_snapshot() -> SecuritySnapshot:
    return security_registry.snapshot()


def invalidate_securities() -> None:
    security_registry.invalidate()
