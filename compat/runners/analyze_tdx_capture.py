from __future__ import annotations

import argparse
import json
from decimal import Decimal
from decimal import InvalidOperation

from compat.tdx_capture import CaptureDecodeError
from compat.tdx_capture import analyze_session


def _parse_integer(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid integer {value!r}; use decimal or a 0x-prefixed command"
        ) from exc


def _parse_price(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid price {value!r}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split and inspect lossless TDX proxy application streams"
    )
    parser.add_argument("session_dir")
    parser.add_argument(
        "--command",
        type=_parse_integer,
        action="append",
        help="only show this command; repeat as needed, for example 0x0547",
    )
    parser.add_argument(
        "--code",
        action="append",
        help="only show requests carrying this six-digit code; repeat as needed",
    )
    parser.add_argument(
        "--search-price",
        type=_parse_price,
        action="append",
        help="search decoded responses for common encodings of this price",
    )
    args = parser.parse_args()

    try:
        artifact = analyze_session(
            args.session_dir,
            commands=set(args.command) if args.command else None,
            codes=set(args.code) if args.code else None,
            search_prices=args.search_price,
        )
    except (CaptureDecodeError, OSError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
