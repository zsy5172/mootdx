from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from mootdx_next import AsyncClient
from mootdx_next import ServerEndpoint

PREFERRED_HQ_HOSTS = [
    ("深圳双线主站9", "110.41.174.169", 7709),
    ("通达信深圳双线主站5", "175.178.128.227", 7709),
    ("通达信广州双线主站6", "116.205.171.132", 7709),
    ("通达信广州双线主站5", "116.205.163.254", 7709),
    ("通达信广州双线主站7", "116.205.183.150", 7709),
]


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


async def _run_quotes(client: AsyncClient, symbols: list[str], concurrency: int, iterations: int) -> list[float]:
    latencies: list[float] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def _task() -> None:
        async with semaphore:
            started = time.perf_counter()
            await client.quotes(symbols)
            latencies.append((time.perf_counter() - started) * 1000)

    await asyncio.gather(*[_task() for _ in range(iterations)])
    return latencies


async def _main(iterations: int, concurrency: int) -> int:
    client = AsyncClient(servers=_preferred_servers())
    try:
        latencies = await _run_quotes(client, ["600036", "000001"], concurrency, iterations)
    finally:
        client.close()

    print(f"iterations={iterations}")
    print(f"concurrency={concurrency}")
    print(f"avg_ms={statistics.fmean(latencies):.3f}")
    print(f"p50_ms={statistics.median(latencies):.3f}")
    print(f"p95_ms={sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]:.3f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    return asyncio.run(_main(args.iterations, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
