from __future__ import annotations

import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

EX_MARKET_CACHE_TTL_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class ExMarket:
    market: int
    category: int
    name: str
    short_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "category": self.category,
            "name": self.name,
            "short_name": self.short_name,
        }


ExMarketSnapshot = tuple[ExMarket, ...]
ExMarketLoader = Callable[[], Iterable[ExMarket]]


class ExMarketRegistry:
    """Process-shareable immutable ExHq market-category directory."""

    def __init__(
        self,
        *,
        ttl_seconds: float = EX_MARKET_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: ExMarketSnapshot = ()
        self._by_market: Mapping[int, ExMarket] = MappingProxyType({})
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    def get(self, loader: ExMarketLoader, *, refresh: bool = False) -> ExMarketSnapshot:
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
            self._by_market = MappingProxyType({item.market: item for item in snapshot})
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return snapshot

    def find(self, market: int) -> ExMarket | None:
        with self._condition:
            if not self._loaded or self._time_fn() >= self._expires_at:
                return None
            return self._by_market.get(int(market))

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    @staticmethod
    def _normalize(values: Iterable[ExMarket]) -> ExMarketSnapshot:
        snapshot = tuple(values)
        seen: set[int] = set()
        for item in snapshot:
            if not isinstance(item, ExMarket):
                raise TypeError("extended market loader must return ExMarket instances")
            if not 0 <= item.market <= 0xFF:
                raise ValueError(f"invalid extended market: {item.market}")
            if not 0 <= item.category <= 0xFF:
                raise ValueError(f"invalid extended market category: {item.category}")
            if item.market in seen:
                raise ValueError(f"duplicate extended market: {item.market}")
            seen.add(item.market)
        return snapshot


ex_market_registry = ExMarketRegistry()
