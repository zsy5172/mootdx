from __future__ import annotations

import argparse
import statistics
import struct
import time

from mootdx.consts import HQ_HOSTS
from mootdx_next.errors import TransportError
from mootdx_next.models import RequestContext
from mootdx_next.models import ServerEndpoint
from mootdx_next.transport.socket_transport import SyncSocketTransport


def _stock_count_request(market: int = 0) -> bytes:
    payload = bytearray.fromhex("0c 0c 18 6c 00 01 08 00 08 00 4e 04")
    payload.extend(struct.pack("<H", market))
    payload.extend(b"\x75\xc7\x33\x01")
    return bytes(payload)


def _pick_server() -> ServerEndpoint:
    label, host, port = HQ_HOSTS[0]
    return ServerEndpoint(host=host, port=port, label=label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--market", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=1500)
    args = parser.parse_args()

    transport = SyncSocketTransport()
    server = _pick_server()
    payload = _stock_count_request(args.market)
    latencies: list[float] = []
    errors = 0

    try:
        for _ in range(args.iterations):
            started = time.perf_counter()
            try:
                transport.send(RequestContext(api="stock_count", timeout_ms=args.timeout_ms), payload, server)
            except TransportError:
                errors += 1
            else:
                latencies.append((time.perf_counter() - started) * 1000)
    finally:
        transport.close()

    print(f"server={server.label} {server.host}:{server.port}")
    print(f"iterations={args.iterations}")
    print(f"errors={errors}")
    if latencies:
        print(f"avg_ms={statistics.fmean(latencies):.3f}")
        print(f"p50_ms={statistics.median(latencies):.3f}")
        print(f"p95_ms={sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]:.3f}")
    print(f"bytes_sent={transport.metrics.bytes_sent}")
    print(f"bytes_received={transport.metrics.bytes_received}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
