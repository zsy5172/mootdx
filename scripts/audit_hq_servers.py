from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from dataclasses import dataclass

from mootdx.consts import HQ_HOSTS
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient

STOCK_SYMBOL = "600036"
INFO_SYMBOL = "600036"
INDEX_SYMBOL = "000001"


@dataclass(slots=True)
class ApiAudit:
    ok: bool
    elapsed_ms: float
    detail: str


@dataclass(slots=True)
class ServerAudit:
    labels: list[str]
    host: str
    port: int
    apis: dict[str, ApiAudit]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.apis.values())


def _unique_servers() -> list[tuple[list[str], str, int]]:
    grouped: dict[tuple[str, int], list[str]] = {}
    for label, host, port in HQ_HOSTS:
        grouped.setdefault((host, int(port)), []).append(label)
    return [(labels, host, port) for (host, port), labels in grouped.items()]


def _run_api(name: str, call) -> tuple[str, ApiAudit]:
    started = time.perf_counter()
    try:
        value = call()
        if name == "stock_count":
            ok = int(value) > 0
            detail = f"count={value}"
        elif name == "finance":
            ok = bool(value)
            detail = f"fields={len(value)}"
        else:
            ok = bool(value)
            detail = f"rows={len(value)}"
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return name, ApiAudit(ok=ok, elapsed_ms=round(elapsed_ms, 2), detail=detail)


def audit_server(item: tuple[list[str], str, int]) -> ServerAudit:
    labels, host, port = item
    endpoint = ServerEndpoint(host=host, port=port, label=labels[0])
    client = SyncClient(servers=[endpoint], max_retries=0)
    apis: dict[str, ApiAudit] = {}
    try:
        name, result = _run_api("stock_count", lambda: client.stock_count(0))
        apis[name] = result
        if not result.ok:
            return ServerAudit(labels=labels, host=host, port=port, apis=apis)

        checks = [
            ("quotes", lambda: client.quotes(STOCK_SYMBOL)),
            ("bars", lambda: client.bars(STOCK_SYMBOL, frequency=9, offset=10)),
            ("index_bars", lambda: client.index_bars(INDEX_SYMBOL, frequency=9, start=0, offset=10, market=1)),
            ("finance", lambda: client.finance(INFO_SYMBOL)),
            ("f10_categories", lambda: client.f10_categories(INFO_SYMBOL)),
        ]
        for api, call in checks:
            name, result = _run_api(api, call)
            apis[name] = result
    finally:
        client.close()

    return ServerAudit(labels=labels, host=host, port=port, apis=apis)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit every unique HQ server with representative APIs.")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--host", action="append", dest="hosts")
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    servers = _unique_servers()
    if args.hosts:
        selected = set(args.hosts)
        servers = [server for server in servers if server[1] in selected]

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        audits = list(executor.map(audit_server, servers))

    api_summary: dict[str, dict[str, int]] = {}
    for audit in audits:
        for api, result in audit.apis.items():
            counts = api_summary.setdefault(api, {"tested": 0, "passed": 0, "failed": 0})
            counts["tested"] += 1
            counts["passed" if result.ok else "failed"] += 1

    reported = [] if args.summary_only else audits
    if args.failures_only and not args.summary_only:
        reported = [audit for audit in audits if not audit.ok]

    payload = {
        "summary": {
            "total": len(audits),
            "passed": sum(audit.ok for audit in audits),
            "failed": sum(not audit.ok for audit in audits),
        },
        "api_summary": api_summary,
        "servers": [{**asdict(audit), "ok": audit.ok} for audit in reported],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
