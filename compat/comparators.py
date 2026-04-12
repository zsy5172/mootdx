from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from compat.common import load_json


def _is_nan_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def _diff_values(left: Any, right: Any, path: str) -> list[str]:
    if _is_nan_like(left) and _is_nan_like(right):
        return []

    if type(left) is not type(right):
        return [f"{path}: type mismatch {type(left).__name__} != {type(right).__name__}"]

    if isinstance(left, dict):
        diffs: list[str] = []
        left_keys = set(left)
        right_keys = set(right)
        for key in sorted(left_keys - right_keys):
            diffs.append(f"{path}.{key}: missing on right")
        for key in sorted(right_keys - left_keys):
            diffs.append(f"{path}.{key}: missing on left")
        for key in sorted(left_keys & right_keys):
            diffs.extend(_diff_values(left[key], right[key], f"{path}.{key}"))
        return diffs

    if isinstance(left, list):
        diffs: list[str] = []
        if len(left) != len(right):
            diffs.append(f"{path}: length mismatch {len(left)} != {len(right)}")
            return diffs
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            diffs.extend(_diff_values(left_item, right_item, f"{path}[{index}]"))
        return diffs

    if left != right:
        return [f"{path}: {left!r} != {right!r}"]

    return []


def compare_payloads(
    left: dict[str, Any],
    right: dict[str, Any],
    profile: str,
) -> list[str]:
    if profile not in {"scalar_exact", "table_exact", "quotes_snapshot_exact"}:
        return [f"unsupported comparator profile: {profile}"]

    diffs: list[str] = []
    for key in ["case_id", "api", "status", "result_type"]:
        diffs.extend(_diff_values(left.get(key), right.get(key), key))

    if left.get("status") == "error" or right.get("status") == "error":
        diffs.extend(_diff_values(left.get("error"), right.get("error"), "error"))
        return diffs

    diffs.extend(_diff_values(left.get("result"), right.get("result"), "result"))
    return diffs


def compare_artifact_files(
    left_path: str | Path,
    right_path: str | Path,
    profile: str | None = None,
) -> list[str]:
    left = load_json(left_path)
    right = load_json(right_path)
    comparator = profile or left.get("comparator") or right.get("comparator") or "scalar_exact"
    return compare_payloads(left, right, comparator)
