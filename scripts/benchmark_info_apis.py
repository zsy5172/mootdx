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


def _run_finance_workload(client: SyncClient, iterations: int) -> tuple[list[float], int]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        client.finance(symbol="000001")
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies, 1


def _run_xdxr_workload(client: SyncClient, iterations: int) -> tuple[list[float], int]:
    latencies: list[float] = []
    row_count = 0
    for _ in range(iterations):
        started = time.perf_counter()
        rows = client.xdxr(symbol="600036")
        latencies.append((time.perf_counter() - started) * 1000)
        row_count = len(rows)
    return latencies, row_count


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
        finance_latencies, finance_row_count = _run_finance_workload(client, args.iterations)
        xdxr_latencies, xdxr_row_count = _run_xdxr_workload(client, args.iterations)
    finally:
        client.connection_pool.close_all()

    _print_stats("finance-000001", finance_latencies, row_count=finance_row_count)
    _print_stats("xdxr-600036", xdxr_latencies, row_count=xdxr_row_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
