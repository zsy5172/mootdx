from compat.comparators import compare_payloads
from compat.deviation_registry import Deviation
from compat.deviation_registry import DeviationRegistry


def test_compare_payloads_reports_nested_result_path() -> None:
    left = {
        "case_id": "quotes/single_sh",
        "api": "quotes",
        "comparator": "quotes_snapshot_exact",
        "status": "ok",
        "result_type": "dataframe",
        "result": {
            "columns": ["code"],
            "records": [{"code": "600036"}],
        },
        "error": None,
    }
    right = {
        "case_id": "quotes/single_sh",
        "api": "quotes",
        "comparator": "quotes_snapshot_exact",
        "status": "ok",
        "result_type": "dataframe",
        "result": {
            "columns": ["code"],
            "records": [{"code": "000001"}],
        },
        "error": None,
    }

    diffs = compare_payloads(left, right, "quotes_snapshot_exact")

    assert diffs == ["result.records[0].code: '600036' != '000001'"]


def test_compare_payloads_reports_error_payload_difference() -> None:
    left = {
        "case_id": "stock_count/sh",
        "api": "stock_count",
        "comparator": "scalar_exact",
        "status": "error",
        "result_type": None,
        "result": None,
        "error": {"type": "RuntimeError", "message": "left"},
    }
    right = {
        "case_id": "stock_count/sh",
        "api": "stock_count",
        "comparator": "scalar_exact",
        "status": "error",
        "result_type": None,
        "result": None,
        "error": {"type": "RuntimeError", "message": "right"},
    }

    diffs = compare_payloads(left, right, "scalar_exact")

    assert diffs == ["error.message: 'left' != 'right'"]


def test_compare_payloads_treats_nan_values_as_equal() -> None:
    left = {
        "case_id": "xdxr/sh_600036",
        "api": "xdxr",
        "comparator": "table_exact",
        "status": "ok",
        "result_type": "dataframe",
        "result": {
            "columns": ["fenhong"],
            "records": [{"fenhong": float("nan")}],
        },
        "error": None,
    }
    right = {
        "case_id": "xdxr/sh_600036",
        "api": "xdxr",
        "comparator": "table_exact",
        "status": "ok",
        "result_type": "dataframe",
        "result": {
            "columns": ["fenhong"],
            "records": [{"fenhong": float("nan")}],
        },
        "error": None,
    }

    assert compare_payloads(left, right, "table_exact") == []


def test_compare_payloads_allows_only_registered_paths() -> None:
    left = {
        "case_id": "case",
        "api": "bars",
        "status": "ok",
        "result_type": "dataframe",
        "result": {"records": [{"close": 1.0}]},
    }
    right = {
        "case_id": "case",
        "api": "bars",
        "status": "ok",
        "result_type": "list",
        "result": {"records": [{"close": 2.0}]},
    }
    registry = DeviationRegistry(
        (
            Deviation(
                id="registered",
                apis=("bars",),
                case_ids=("case",),
                legacy_behavior="legacy",
                next_behavior="next",
                rationale="reason",
                evidence=("evidence",),
                regression_tests=("test",),
                allowed_paths=("result",),
                status="accepted",
            ),
        ),
        baseline={"python": "3.11", "mootdx": "0.11.7", "tdxpy": "0.2.7"},
        version=1,
    )

    assert compare_payloads(
        left,
        right,
        "table_exact",
        deviation_id="registered",
        registry=registry,
    ) == ["result_type: 'dataframe' != 'list'"]
