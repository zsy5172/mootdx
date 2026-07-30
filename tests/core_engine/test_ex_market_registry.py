from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from mootdx_next.ex_markets import ExMarket
from mootdx_next.ex_markets import ExMarketRegistry


def test_ex_market_registry_keeps_an_immutable_indexed_snapshot() -> None:
    registry = ExMarketRegistry()
    snapshot = registry.get(
        lambda: [
            ExMarket(31, 2, "香港主板", "KH"),
            ExMarket(28, 3, "郑州商品", "QZ"),
        ]
    )

    assert isinstance(snapshot, tuple)
    assert registry.find(31) is snapshot[0]
    assert registry.find(28) is snapshot[1]


def test_ex_market_registry_loads_once_across_threads() -> None:
    registry = ExMarketRegistry()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def loader():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return [ExMarket(31, 2, "香港主板", "KH")]

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(registry.get, loader) for _ in range(3)]
        assert started.wait(timeout=5)
        release.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
