from __future__ import annotations

import json
from pathlib import Path

import pytest

from compat.deviation_registry import DeviationRegistry


def test_committed_deviation_registry_is_valid_and_pins_baseline() -> None:
    registry = DeviationRegistry.load()

    assert registry.version == 1
    assert registry.baseline == {"python": "3.11", "mootdx": "0.11.7", "tdxpy": "0.2.7"}
    assert registry.require("historical-kline-date-pagination").status == "accepted"


def test_deviation_filter_is_default_deny() -> None:
    registry = DeviationRegistry.load()
    diffs = ["result.records[0].close: 1.0 != 2.0"]

    assert registry.filter_diffs(
        diffs,
        deviation_id=None,
        api="get_k_data",
        case_id="historical_date_window",
    ) == diffs


def test_deviation_filter_requires_matching_api_case_and_path() -> None:
    registry = DeviationRegistry.load()
    diffs = [
        "result.records[0].close: 1.0 != 2.0",
        "result_type: DataFrame != list",
    ]

    assert registry.filter_diffs(
        diffs,
        deviation_id="historical-kline-date-pagination",
        api="get_k_data",
        case_id="historical_date_window",
    ) == ["result_type: DataFrame != list"]


def test_deviation_registry_rejects_unknown_id() -> None:
    registry = DeviationRegistry.load()

    with pytest.raises(ValueError, match="unknown compatibility deviation"):
        registry.require("not-registered")


def test_deviation_registry_rejects_unpinned_baseline(tmp_path) -> None:
    payload = json.loads(Path("compat/deviations/v1.json").read_text(encoding="utf-8"))
    payload["baseline"]["mootdx"] = "latest"
    path = tmp_path / "deviations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must pin"):
        DeviationRegistry.load(path)
