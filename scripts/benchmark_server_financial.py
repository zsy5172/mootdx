from __future__ import annotations

import argparse
import statistics
import time

from mootdx.financial.financial import FinancialList
from mootdx.server import server


def _run_server_probe(iterations: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        server(index="HQ", limit=1, sync=True)
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def _run_financial_listing(iterations: int) -> list[float]:
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        try:
            FinancialList().fetch_and_parse()
        except Exception:
            break
        latencies.append((time.perf_counter() - started) * 1000)
    return latencies


def _print_stats(label: str, latencies: list[float]) -> None:
    print(f"[{label}]")
    if not latencies:
        print("no successful samples")
        return
    print(f"iterations={len(latencies)}")
    print(f"avg_ms={statistics.fmean(latencies):.3f}")
    print(f"p50_ms={statistics.median(latencies):.3f}")
    print(f"p95_ms={sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]:.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()

    _print_stats("server-hq-probe", _run_server_probe(args.iterations))
    _print_stats("financial-list", _run_financial_listing(args.iterations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
