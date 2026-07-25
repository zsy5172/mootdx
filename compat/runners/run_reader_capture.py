from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path

from compat.common import write_json
from compat.reader_matrix import capture_reader_case
from compat.reader_matrix import READER_CASES


def _baseline_identity(runtime: str) -> dict[str, str]:
    if runtime != "legacy":
        return {"runtime": runtime}
    actual = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "mootdx": importlib.metadata.version("mootdx"),
        "tdxpy": importlib.metadata.version("tdxpy"),
    }
    expected = {"python": "3.11", "mootdx": "0.11.7", "tdxpy": "0.2.7"}
    if actual != expected:
        raise RuntimeError(f"reader baseline identity mismatch: expected {expected}, got {actual}")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=["legacy", "next"], required=True)
    parser.add_argument("--tdxdir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    baseline = _baseline_identity(args.runtime)
    for case in READER_CASES:
        artifact = capture_reader_case(
            case,
            runtime=args.runtime,
            tdxdir=args.tdxdir,
        )
        artifact["backend"] = f"mootdx-{args.runtime}-reader"
        artifact["baseline"] = baseline
        output = output_dir / f"{case.case_id}.json"
        write_json(output, artifact)
        print(f"[{artifact['status'].upper()}] {case.case_id} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
