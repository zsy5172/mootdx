from __future__ import annotations

import argparse
from pathlib import Path

from compat.comparators import compare_artifact_files
from compat.comparators import compare_payloads
from compat.common import write_json
from compat.registry import build_live_artifact
from compat.registry import build_live_decode_artifact_pair
from compat.registry import capture_corpus_case
from compat.registry import corpus_case_dir
from compat.registry import expected_path
from compat.registry import generated_spec_paths
from compat.registry import list_spec_paths
from compat.registry import load_spec
from compat.registry import replay_case
from compat.matrix import coverage_summary
from compat.matrix import write_specs


def _capture_live_artifact(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    artifact = build_live_artifact(spec, runtime=args.runtime)
    if args.backend_name:
        artifact["backend"] = args.backend_name
    if args.output:
        write_json(args.output, artifact)
    else:
        print(artifact)
    return 0


def _capture_corpus(args: argparse.Namespace) -> int:
    spec_paths = [Path(args.spec)] if args.spec else list_spec_paths(args.spec_root)
    for path in spec_paths:
        spec = load_spec(path)
        artifact = capture_corpus_case(spec, args.corpus_root)
        print(f"captured {spec['api']}/{spec['case_id']} -> {corpus_case_dir(args.corpus_root, spec)}")
        if args.artifacts_dir:
            output = Path(args.artifacts_dir) / f"{spec['api']}__{spec['case_id']}__captured.json"
            artifact["backend"] = args.backend_name
            write_json(output, artifact)
    return 0


def _compare_live_decode(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    left, right = build_live_decode_artifact_pair(spec)
    left["backend"] = args.left_backend_name
    right["backend"] = args.right_backend_name

    if args.left_output:
        write_json(args.left_output, left)
    if args.right_output:
        write_json(args.right_output, right)

    diffs = compare_payloads(left, right, spec["comparator"])
    if diffs:
        print(f"[FAIL] {spec['api']}/{spec['case_id']}")
        for diff in diffs:
            print(f"  - {diff}")
        return 1

    print(f"[OK] {spec['api']}/{spec['case_id']}")
    return 0


def _replay(args: argparse.Namespace) -> int:
    spec_paths = [Path(args.spec)] if args.spec else list_spec_paths(args.spec_root)
    failed = False
    for path in spec_paths:
        spec = load_spec(path)
        artifact = replay_case(spec, args.corpus_root, runtime=args.runtime)
        artifact["backend"] = args.backend_name

        expected = expected_path(args.corpus_root, spec)
        if args.artifacts_dir:
            output = Path(args.artifacts_dir) / f"{spec['api']}__{spec['case_id']}__replay.json"
            write_json(output, artifact)
            actual_path = output
        else:
            actual_path = Path(args.corpus_root) / spec["api"] / spec["case_id"] / ".tmp_actual.json"
            write_json(actual_path, artifact)

        diffs = compare_artifact_files(actual_path, expected)
        if actual_path.name == ".tmp_actual.json":
            actual_path.unlink(missing_ok=True)

        if diffs:
            failed = True
            print(f"[FAIL] {spec['api']}/{spec['case_id']}")
            for diff in diffs:
                print(f"  - {diff}")
        else:
            print(f"[OK] {spec['api']}/{spec['case_id']}")

    return 1 if failed else 0


def _generate_specs(args: argparse.Namespace) -> int:
    written = write_specs(args.spec_root)
    print(f"generated {len(written)} specs under {args.spec_root}")
    if args.print_paths:
        for path in generated_spec_paths(args.spec_root):
            print(path)
    if args.print_coverage:
        for api, dimensions in sorted(coverage_summary().items()):
            print(f"[{api}]")
            for dimension, values in sorted(dimensions.items()):
                print(f"  {dimension}: {values}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_live = subparsers.add_parser("capture-live-artifact")
    capture_live.add_argument("--spec", required=True)
    capture_live.add_argument("--output")
    capture_live.add_argument("--runtime", choices=["legacy", "next"], default="legacy")
    capture_live.add_argument("--backend-name", default="mootdx-runtime")
    capture_live.set_defaults(func=_capture_live_artifact)

    capture_corpus = subparsers.add_parser("capture-corpus")
    capture_corpus.add_argument("--spec")
    capture_corpus.add_argument("--spec-root", default="compat/specs")
    capture_corpus.add_argument("--corpus-root", default="compat/corpus")
    capture_corpus.add_argument("--artifacts-dir")
    capture_corpus.add_argument("--backend-name", default="mootdx-runtime")
    capture_corpus.set_defaults(func=_capture_corpus)

    compare_live_decode = subparsers.add_parser("compare-live-decode")
    compare_live_decode.add_argument("--spec", required=True)
    compare_live_decode.add_argument("--left-output")
    compare_live_decode.add_argument("--right-output")
    compare_live_decode.add_argument("--left-backend-name", default="mootdx-legacy-raw")
    compare_live_decode.add_argument("--right-backend-name", default="mootdx-next-raw")
    compare_live_decode.set_defaults(func=_compare_live_decode)

    replay = subparsers.add_parser("replay-corpus")
    replay.add_argument("--spec")
    replay.add_argument("--spec-root", default="compat/specs")
    replay.add_argument("--corpus-root", default="compat/corpus")
    replay.add_argument("--artifacts-dir")
    replay.add_argument("--runtime", choices=["legacy", "next"], default="legacy")
    replay.add_argument("--backend-name", default="compat-replay")
    replay.set_defaults(func=_replay)

    generate_specs = subparsers.add_parser("generate-specs")
    generate_specs.add_argument("--spec-root", default="compat/specs")
    generate_specs.add_argument("--print-paths", action="store_true")
    generate_specs.add_argument("--print-coverage", action="store_true")
    generate_specs.set_defaults(func=_generate_specs)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
