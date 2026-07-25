from __future__ import annotations

import argparse

from compat.comparators import compare_artifact_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--deviation")
    args = parser.parse_args()

    diffs = compare_artifact_files(
        args.left,
        args.right,
        profile=args.profile,
        deviation_id=args.deviation,
    )

    if diffs:
        print("Artifacts differ")
        for diff in diffs:
            print(f"- {diff}")
        return 1

    print("Artifacts match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
