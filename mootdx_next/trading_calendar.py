from __future__ import annotations

import threading
import time
from bisect import bisect_left
from bisect import bisect_right
from collections.abc import Callable
from collections.abc import Iterable

TRADING_CALENDAR_TTL_SECONDS = 10 * 60
TradingDaySnapshot = tuple[str, ...]
TradingDayLoader = Callable[[], Iterable[str]]


class TradingCalendarRegistry:
    """Thread-safe process calendar backed by an immutable date snapshot."""

    def __init__(
        self,
        *,
        ttl_seconds: float = TRADING_CALENDAR_TTL_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = float(ttl_seconds)
        self._time_fn = time_fn
        self._condition = threading.Condition()
        self._snapshot: TradingDaySnapshot = ()
        self._date_set: frozenset[str] = frozenset()
        self._expires_at = 0.0
        self._loaded = False
        self._refreshing = False

    def get(
        self,
        loader: TradingDayLoader,
        *,
        refresh: bool = False,
    ) -> TradingDaySnapshot:
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
            self._date_set = frozenset(snapshot)
            self._expires_at = self._time_fn() + self._ttl_seconds
            self._loaded = True
            self._refreshing = False
            self._condition.notify_all()
            return snapshot

    def contains(self, value: str) -> bool:
        with self._condition:
            return value in self._date_set

    def between(self, start: str, end: str) -> TradingDaySnapshot:
        with self._condition:
            left = bisect_left(self._snapshot, start)
            right = bisect_right(self._snapshot, end)
            return self._snapshot[left:right]

    def snapshot(self) -> TradingDaySnapshot:
        with self._condition:
            return self._snapshot

    def invalidate(self) -> None:
        with self._condition:
            self._expires_at = 0.0

    @staticmethod
    def _normalize(values: Iterable[str]) -> TradingDaySnapshot:
        normalized: set[str] = set()
        for value in values:
            day = str(value)
            if len(day) != 8 or not day.isdigit():
                raise ValueError(f"invalid trading day: {value}")
            normalized.add(day)
        return tuple(sorted(normalized))


trading_calendar_registry = TradingCalendarRegistry()


def trading_calendar_snapshot() -> TradingDaySnapshot:
    return trading_calendar_registry.snapshot()


def invalidate_trading_calendar() -> None:
    trading_calendar_registry.invalidate()
