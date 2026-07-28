from __future__ import annotations

import threading
import time
from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from decimal import ROUND_HALF_UP

from mootdx_next.constants import MARKET_BJ
from mootdx_next.constants import MARKET_SH
from mootdx_next.constants import MARKET_SZ

LIMIT_PRICE_CACHE_TTL_SECONDS = 10 * 60
PRICE_TICK = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PriceLimit:
    market: int
    code: str
    limit_up: float
    limit_down: float

    def to_dict(self, *, source: str = "server") -> dict[str, object]:
        return {
            "market": self.market,
            "code": self.code,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "source": source,
        }


PriceLimitSnapshot = tuple[PriceLimit, ...]
PriceLimitLoader = Callable[[], Iterable[dict[str, object] | PriceLimit]]


class PriceLimitRegistry:
    """Process-local, thread-safe cache for the server's special-price table."""

    def __init__(
        self,
        ttl_seconds: float = LIMIT_PRICE_CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._prices: PriceLimitSnapshot = ()
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def get(self, loader: PriceLimitLoader) -> PriceLimitSnapshot:
        return self._load(loader, force=False)

    def refresh(self, loader: PriceLimitLoader) -> PriceLimitSnapshot:
        return self._load(loader, force=True)

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    def snapshot(self) -> PriceLimitSnapshot:
        with self._condition:
            return self._prices

    def _load(self, loader: PriceLimitLoader, *, force: bool) -> PriceLimitSnapshot:
        with self._condition:
            if self._refreshing:
                self._condition.wait_for(lambda: not self._refreshing)
                if self._loaded:
                    return self._prices

            if not force and self._loaded and self._time_fn() < self._expires_at:
                return self._prices

            self._refreshing = True

        try:
            prices = self._normalize(loader())
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._prices = prices
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return self._prices

    @staticmethod
    def _normalize(
        prices: Iterable[dict[str, object] | PriceLimit],
    ) -> PriceLimitSnapshot:
        normalized: list[PriceLimit] = []
        seen: set[tuple[int, str]] = set()

        for price in prices:
            if isinstance(price, PriceLimit):
                item = price
            else:
                item = PriceLimit(
                    market=int(price["market"]),
                    code=str(price["code"]).zfill(6),
                    limit_up=float(price["limit_up"]),
                    limit_down=float(price["limit_down"]),
                )

            key = (item.market, item.code)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

        return tuple(normalized)


def calculate_normal_stock_price_limit(
    market: int,
    code: str,
    previous_close: float,
) -> PriceLimit | None:
    """Calculate current ordinary A-share limits; special securities return None."""

    rate = _normal_stock_limit_rate(market, code)
    if rate is None or previous_close <= 0:
        return None

    close = Decimal(str(previous_close))
    limit_up = (close * (Decimal("1") + rate)).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
    limit_down = (close * (Decimal("1") - rate)).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
    return PriceLimit(
        market=market,
        code=code,
        limit_up=float(limit_up),
        limit_down=float(limit_down),
    )


def _normal_stock_limit_rate(market: int, code: str) -> Decimal | None:
    if market == MARKET_SH:
        if code.startswith(("688", "689")):
            return Decimal("0.20")
        if code.startswith("60"):
            return Decimal("0.10")
        return None

    if market == MARKET_SZ:
        if code.startswith(("300", "301")):
            return Decimal("0.20")
        if code.startswith(("000", "001", "002", "003")):
            return Decimal("0.10")
        return None

    if market == MARKET_BJ and code.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return Decimal("0.30")

    return None


_price_limit_registry = PriceLimitRegistry()


def get_price_limit_snapshot(loader: PriceLimitLoader) -> PriceLimitSnapshot:
    return _price_limit_registry.get(loader)


def refresh_price_limit_snapshot(loader: PriceLimitLoader) -> PriceLimitSnapshot:
    return _price_limit_registry.refresh(loader)


def invalidate_price_limit_cache() -> None:
    _price_limit_registry.invalidate()


def price_limit_snapshot() -> PriceLimitSnapshot:
    return _price_limit_registry.snapshot()
