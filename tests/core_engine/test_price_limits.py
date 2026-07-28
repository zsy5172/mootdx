from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mootdx_next.limits import calculate_normal_stock_price_limit
from mootdx_next.limits import PriceLimit
from mootdx_next.limits import PriceLimitRegistry


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_price_limit_registry_reuses_immutable_snapshot_until_ttl() -> None:
    clock = Clock()
    calls = []
    registry = PriceLimitRegistry(ttl_seconds=600, time_fn=clock)

    def load():
        calls.append(clock())
        return [
            {"market": 1, "code": "600053", "limit_up": 7.5, "limit_down": 6.14},
            {"market": 1, "code": "600053", "limit_up": 0, "limit_down": 0},
        ]

    first = registry.get(load)
    assert first == (PriceLimit(1, "600053", 7.5, 6.14),)
    assert registry.get(load) is first
    assert len(calls) == 1

    clock.advance(600)
    assert registry.get(load) is not first
    assert len(calls) == 2


def test_price_limit_registry_allows_only_one_concurrent_load() -> None:
    started = threading.Event()
    release = threading.Event()
    load_count = 0
    lock = threading.Lock()
    registry = PriceLimitRegistry()

    def load():
        nonlocal load_count
        with lock:
            load_count += 1
        started.set()
        assert release.wait(timeout=5)
        return [{"market": 0, "code": "000010", "limit_up": 1.62, "limit_down": 1.32}]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get, load) for _ in range(4)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert load_count == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_price_limit_registry_supports_refresh_and_invalidation() -> None:
    calls = []
    registry = PriceLimitRegistry()

    def load():
        calls.append(len(calls) + 1)
        return [{"market": 1, "code": "600053", "limit_up": calls[-1], "limit_down": 0}]

    assert registry.get(load)[0].limit_up == 1
    assert registry.refresh(load)[0].limit_up == 2
    assert registry.get(load)[0].limit_up == 2

    registry.invalidate()
    assert registry.get(load)[0].limit_up == 3


def test_price_limit_registry_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        PriceLimitRegistry(ttl_seconds=0)


@pytest.mark.parametrize(
    ("market", "code", "close", "expected"),
    [
        (1, "600036", 39.0, (42.9, 35.1)),
        (0, "000001", 10.05, (11.06, 9.05)),
        (0, "300010", 1.92, (2.3, 1.54)),
        (1, "688981", 10.0, (12.0, 8.0)),
        (2, "920001", 10.0, (13.0, 7.0)),
    ],
)
def test_calculate_normal_stock_price_limit_uses_board_rate_and_half_up_rounding(
    market: int,
    code: str,
    close: float,
    expected: tuple[float, float],
) -> None:
    result = calculate_normal_stock_price_limit(market, code, close)

    assert result is not None
    assert (result.limit_up, result.limit_down) == expected


@pytest.mark.parametrize(
    ("market", "code"),
    [(1, "510500"), (1, "110074"), (0, "159915"), (0, "200001"), (2, "810011")],
)
def test_calculate_normal_stock_price_limit_does_not_guess_special_instruments(
    market: int,
    code: str,
) -> None:
    assert calculate_normal_stock_price_limit(market, code, 10.0) is None
