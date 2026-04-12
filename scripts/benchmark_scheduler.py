from __future__ import annotations

import argparse
import statistics
import time

from mootdx_next.errors import NoHealthyServerError
from mootdx_next.errors import PoolExhaustedError
from mootdx_next.errors import TransportError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.scheduler.pools import ConnectionPool
from mootdx_next.scheduler.pools import ServerPool

PREFERRED_HQ_HOSTS = [
    ("深圳双线主站9", "110.41.174.169", 7709),
    ("通达信深圳双线主站5", "175.178.128.227", 7709),
    ("通达信广州双线主站6", "116.205.171.132", 7709),
    ("通达信广州双线主站5", "116.205.163.254", 7709),
    ("通达信广州双线主站7", "116.205.183.150", 7709),
]


def _stock_count_request() -> bytes:
    return bytes.fromhex("0c0c186c0001080008004e04000075c73301")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--timeout-ms", type=int, default=1500)
    args = parser.parse_args()

    servers = [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]
    payload = _stock_count_request()
    context = RequestContext(api="stock_count", timeout_ms=args.timeout_ms)
    connection_pool = ConnectionPool(max_connections_per_server=2)
    server_pool = ServerPool(servers, connection_pool=connection_pool, failure_threshold=1, cooldown_ms=60_000)
    latencies: list[float] = []
    errors = 0

    try:
        for _ in range(args.iterations):
            started = time.perf_counter()
            try:
                server = server_pool.select(context)
                lease = connection_pool.acquire(server)
                envelope = lease.transport.send(context, payload, server)
                server_pool.mark_success(server, lease.transport.metrics)
                connection_pool.release(lease)
                latencies.append(envelope.elapsed_ms or ((time.perf_counter() - started) * 1000))
            except (NoHealthyServerError, PoolExhaustedError, TransportError) as exc:
                errors += 1
                if "lease" in locals():
                    connection_pool.discard(lease)
                if "server" in locals():
                    server_pool.mark_failure(server, exc)
    finally:
        connection_pool.close_all()

    print(f"iterations={args.iterations}")
    print(f"errors={errors}")
    if latencies:
        print(f"avg_ms={statistics.fmean(latencies):.3f}")
        print(f"p50_ms={statistics.median(latencies):.3f}")
        print(f"p95_ms={sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]:.3f}")
    print(f"pool={connection_pool.snapshot()}")
    print(f"servers={server_pool.snapshot()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
