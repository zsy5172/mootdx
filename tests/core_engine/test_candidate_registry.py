from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mootdx_next.candidates import CandidateRegistry
from mootdx_next.candidates import ServerCandidate
import mootdx_next.candidates as candidates_module


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_registry_is_lazy_and_reuses_immutable_snapshot_until_ttl_expires() -> None:
    clock = Clock()
    calls = []

    def load():
        calls.append(clock())
        return [
            ServerCandidate(host="1.1.1.1", port=7709, label="first"),
            ServerCandidate(host="1.1.1.1", port=7709, label="duplicate"),
        ]

    registry = CandidateRegistry(load, ttl_seconds=600, time_fn=clock)

    assert registry.snapshot() == ()
    assert calls == []

    first = registry.get()
    assert isinstance(first, tuple)
    assert first == (ServerCandidate(host="1.1.1.1", port=7709, label="first"),)
    assert registry.get() is first
    assert len(calls) == 1

    clock.advance(599)
    assert registry.get() is first
    assert len(calls) == 1

    clock.advance(1)
    second = registry.get()
    assert second == first
    assert second is not first
    assert len(calls) == 2


def test_registry_supports_force_refresh_and_invalidation() -> None:
    calls = []

    def load():
        calls.append(len(calls) + 1)
        return [ServerCandidate(host=f"10.0.0.{calls[-1]}", port=7709)]

    registry = CandidateRegistry(load)

    assert registry.get()[0].host == "10.0.0.1"
    assert registry.refresh()[0].host == "10.0.0.2"
    assert registry.get()[0].host == "10.0.0.2"

    registry.invalidate()
    assert registry.get()[0].host == "10.0.0.3"


def test_registry_allows_only_one_concurrent_initial_load() -> None:
    loader_started = threading.Event()
    release_loader = threading.Event()
    load_count = 0
    count_lock = threading.Lock()

    def load():
        nonlocal load_count
        with count_lock:
            load_count += 1
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return [ServerCandidate(host="1.1.1.1", port=7709)]

    registry = CandidateRegistry(load)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(registry.get) for _ in range(4)]
        assert loader_started.wait(timeout=5)
        release_loader.set()
        snapshots = [future.result(timeout=5) for future in futures]

    assert load_count == 1
    assert all(snapshot is snapshots[0] for snapshot in snapshots)


def test_registry_rejects_non_positive_ttl() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        CandidateRegistry(lambda: (), ttl_seconds=0)


def test_native_probe_filters_failed_capabilities_and_sorts_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = (
        ("slow", "1.1.1.1", 7709),
        ("failed", "2.2.2.2", 7709),
        ("fast", "3.3.3.3", 7709),
    )

    def probe(label: str, host: str, port: int):
        if label == "failed":
            return None
        latency = 30.0 if label == "slow" else 10.0
        return ServerCandidate(host=host, port=port, label=label, latency_ms=latency)

    monkeypatch.setattr(candidates_module, "HQ_HOSTS", hosts)
    monkeypatch.setattr(candidates_module, "_probe_one_hq_candidate", probe)

    assert candidates_module._probe_hq_candidates() == (
        ServerCandidate(host="3.3.3.3", port=7709, label="fast", latency_ms=10.0),
        ServerCandidate(host="1.1.1.1", port=7709, label="slow", latency_ms=30.0),
    )


def test_native_probe_keeps_fund_flow_capability_in_the_ranked_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = tuple(
        (f"generic-{index}", f"10.0.0.{index}", 7709)
        for index in range(1, 6)
    ) + (("funds", "182.140.139.191", 7709),)

    def probe(label: str, host: str, port: int):
        latency = 100.0 if label == "funds" else float(host.rsplit(".", 1)[-1])
        return ServerCandidate(host=host, port=port, label=label, latency_ms=latency)

    monkeypatch.setattr(candidates_module, "HQ_HOSTS", hosts)
    monkeypatch.setattr(candidates_module, "_probe_one_hq_candidate", probe)

    result = candidates_module._probe_hq_candidates()

    assert len(result) == candidates_module.HQ_CANDIDATE_LIMIT
    assert [(item.host, item.port) for item in result][-1] == ("182.140.139.191", 7709)


def test_native_probe_reserves_every_healthy_supplemental_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fund_hosts = (
        ("funds-1", "182.140.139.191", 7709),
        ("funds-2", "119.6.200.40", 7709),
        ("funds-3", "218.200.222.134", 7709),
        ("funds-4", "182.150.28.166", 7709),
    )
    hosts = (
        ("generic-1", "10.0.0.1", 7709),
        ("generic-2", "10.0.0.2", 7709),
        ("generic-3", "10.0.0.3", 7709),
        *fund_hosts,
    )

    def probe(label: str, host: str, port: int):
        latency = float(host.rsplit(".", 1)[-1])
        return ServerCandidate(host=host, port=port, label=label, latency_ms=latency)

    monkeypatch.setattr(candidates_module, "HQ_HOSTS", hosts)
    monkeypatch.setattr(candidates_module, "_probe_one_hq_candidate", probe)

    result = candidates_module._probe_hq_candidates()

    assert len(result) == candidates_module.HQ_CANDIDATE_LIMIT
    assert {
        (item.host, item.port)
        for item in result
        if (item.host, item.port) in candidates_module.FUND_FLOW_HOSTS
    } == {(host, port) for _, host, port in fund_hosts}
