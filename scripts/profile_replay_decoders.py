from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import time
from pathlib import Path
from typing import Any
from typing import Callable

from mootdx_next import bars_to_frame
from mootdx_next import finance_to_frame
from mootdx_next import quotes_to_frame
from mootdx_next import transactions_to_frame
from mootdx_next import xdxr_to_frame
from mootdx_next import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "compat" / "corpus"


def _load_body(api: str, case_id: str, step_id: str) -> bytes:
    return (CORPUS_ROOT / api / case_id / "steps" / step_id / "response.body.bin").read_bytes()


def _profile_workload(
    label: str,
    decode: Callable[[], Any],
    adapter: Callable[[Any], Any],
    iterations: int,
    top: int,
) -> None:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    last_result: Any = None
    for _ in range(iterations):
        decoded = decode()
        last_result = adapter(decoded)
    profiler.disable()
    elapsed_ms = (time.perf_counter() - started) * 1000

    stats_stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stats_stream).sort_stats("cumulative")
    stats.print_stats(top)

    if hasattr(last_result, "__len__") and not isinstance(last_result, (str, bytes, dict)):
        row_count = len(last_result)
    elif isinstance(last_result, dict):
        row_count = 1
    else:
        row_count = 1

    print(f"[{label}]")
    print(f"iterations={iterations}")
    print(f"elapsed_ms={elapsed_ms:.3f}")
    print(f"avg_decode_ms={elapsed_ms / iterations:.6f}")
    print(f"rows_per_sec={(row_count * iterations) / (elapsed_ms / 1000):.3f}")
    print(stats_stream.getvalue().strip())
    print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    protocol = StdQuoteProtocol()

    quotes_body = _load_body("quotes", "mixed_batch", "01_quotes")
    bars_body = _load_body("bars", "daily_sh_600036_last10", "01_bars")
    transactions_body = _load_body("transactions", "history_sh_600036_20170209_last10", "01_transactions")
    finance_body = _load_body("finance", "sz_000001", "01_finance")
    xdxr_body = _load_body("xdxr", "sh_600036", "01_xdxr")

    _profile_workload(
        "quotes-mixed-batch",
        lambda: protocol.decode_quotes(quotes_body),
        quotes_to_frame,
        iterations=args.iterations,
        top=args.top,
    )
    _profile_workload(
        "bars-daily-10",
        lambda: protocol.decode_bars(bars_body, frequency=9),
        bars_to_frame,
        iterations=args.iterations,
        top=args.top,
    )
    _profile_workload(
        "transactions-history-10",
        lambda: protocol.decode_history_transactions(transactions_body),
        transactions_to_frame,
        iterations=args.iterations,
        top=args.top,
    )
    _profile_workload(
        "finance-single-row",
        lambda: protocol.decode_finance(finance_body),
        finance_to_frame,
        iterations=args.iterations,
        top=args.top,
    )
    _profile_workload(
        "xdxr-66-rows",
        lambda: protocol.decode_xdxr(xdxr_body),
        xdxr_to_frame,
        iterations=args.iterations,
        top=args.top,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
