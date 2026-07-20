from __future__ import annotations

import argparse
import statistics
import time

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient

PREFERRED_HQ_HOSTS = [
    ("深圳双线主站9", "110.41.174.169", 7709),
    ("通达信深圳双线主站5", "175.178.128.227", 7709),
    ("通达信广州双线主站6", "116.205.171.132", 7709),
    ("通达信广州双线主站5", "116.205.163.254", 7709),
    ("通达信广州双线主站7", "116.205.183.150", 7709),
]


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


def _run_bars_workload(client: SyncClient, iterations: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        client.bars(symbol="600036", frequency="day", offset=10)
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def _run_intraday_bars_workload(client: SyncClient, iterations: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        client.bars(symbol="600036", frequency="5m", offset=20)
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def _run_minutes_workload(client: SyncClient, iterations: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        client.minutes(symbol="000001", date="20171010")
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def _print_stats(label: str, latencies: list[float], row_count: int) -> None:
    print(f"[{label}]")
    print(f"iterations={len(latencies)}")
    print(f"avg_ms={statistics.fmean(latencies):.3f}")
    print(f"p50_ms={statistics.median(latencies):.3f}")
    print(f"p95_ms={sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]:.3f}")
    print(f"rows_per_sec={(row_count * len(latencies)) / (sum(latencies) / 1000):.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()

    client = SyncClient(servers=_preferred_servers())
    try:
        daily_latencies = _run_bars_workload(client, args.iterations)
        intraday_latencies = _run_intraday_bars_workload(client, args.iterations)
        minutes_latencies = _run_minutes_workload(client, args.iterations)
    finally:
        client.connection_pool.close_all()

    _print_stats("bars-daily-10", daily_latencies, row_count=10)
    _print_stats("bars-5m-20", intraday_latencies, row_count=20)
    _print_stats("minutes-history-240", minutes_latencies, row_count=240)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
