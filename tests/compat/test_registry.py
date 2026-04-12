from __future__ import annotations

import struct
from pathlib import Path

from compat.common import load_json
from compat.common import write_json
from compat.registry import build_live_decode_artifact_pair
from compat.registry import list_spec_paths
from compat.registry import replay_case


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_list_spec_paths_ignores_non_json_files(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    (specs / "stock_count").mkdir(parents=True)
    (specs / "stock_count" / "sh.json").write_text("{}", encoding="utf-8")
    (specs / "README.md").write_text("ignored", encoding="utf-8")

    paths = list_spec_paths(specs)

    assert paths == [specs / "stock_count" / "sh.json"]


def test_replay_stock_count_case_from_corpus(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh",
        "api": "stock_count",
        "comparator": "scalar_exact",
        "call": {"kwargs": {"market": 1}},
    }
    case_dir = tmp_path / "corpus" / "stock_count" / "sh"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh",
            "api": "stock_count",
            "comparator": "scalar_exact",
            "steps": [
                {
                    "id": "01_stock_count",
                    "parser": "GetSecurityCountCmd",
                    "kwargs": {"market": 1},
                    "request_path": "steps/01_stock_count/request.bin",
                    "response_body_path": "steps/01_stock_count/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    _write_bytes(case_dir / "steps" / "01_stock_count" / "request.bin", b"req")
    _write_bytes(case_dir / "steps" / "01_stock_count" / "response.body.bin", struct.pack("<H", 22981))

    artifact = replay_case(spec, tmp_path / "corpus")

    assert artifact["status"] == "ok"
    assert artifact["result"] == 22981
    assert artifact["result_type"] == "int"


def test_replay_stock_count_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh",
        "api": "stock_count",
        "comparator": "scalar_exact",
        "call": {"kwargs": {"market": 1}},
    }
    case_dir = tmp_path / "corpus" / "stock_count" / "sh"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh",
            "api": "stock_count",
            "comparator": "scalar_exact",
            "steps": [
                {
                    "id": "01_stock_count",
                    "parser": "GetSecurityCountCmd",
                    "kwargs": {"market": 1},
                    "request_path": "steps/01_stock_count/request.bin",
                    "response_body_path": "steps/01_stock_count/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    _write_bytes(case_dir / "steps" / "01_stock_count" / "request.bin", b"req")
    _write_bytes(case_dir / "steps" / "01_stock_count" / "response.body.bin", struct.pack("<H", 22981))

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result"] == 22981
    assert artifact["result_type"] == "int"


def test_replay_stocks_call_chain_from_corpus(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh_full_market",
        "api": "stocks",
        "comparator": "table_exact",
        "call": {"kwargs": {"market": 1}},
    }
    case_dir = tmp_path / "corpus" / "stocks" / "sh_full_market"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh_full_market",
            "api": "stocks",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_stock_count",
                    "parser": "GetSecurityCountCmd",
                    "kwargs": {"market": 1},
                    "request_path": "steps/01_stock_count/request.bin",
                    "response_body_path": "steps/01_stock_count/response.body.bin",
                },
                {
                    "id": "02_list_00000",
                    "parser": "GetSecurityList",
                    "kwargs": {"market": 1, "start": 0},
                    "request_path": "steps/02_list_00000/request.bin",
                    "response_body_path": "steps/02_list_00000/response.body.bin",
                },
            ],
            "expected_path": "expected.json",
        },
    )
    _write_bytes(case_dir / "steps" / "01_stock_count" / "request.bin", b"req1")
    _write_bytes(case_dir / "steps" / "01_stock_count" / "response.body.bin", struct.pack("<H", 1))

    list_body = bytearray(struct.pack("<H", 1))
    list_body.extend(
        struct.pack(
            "<6sH8s4sBI4s",
            b"600036",
            100,
            "PINGAN".encode("gbk").ljust(8, b"\x00"),
            b"\x00\x00\x00\x00",
            2,
            1234,
            b"\x00\x00\x00\x00",
        )
    )
    _write_bytes(case_dir / "steps" / "02_list_00000" / "request.bin", b"req2")
    _write_bytes(case_dir / "steps" / "02_list_00000" / "response.body.bin", bytes(list_body))

    artifact = replay_case(spec, tmp_path / "corpus")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["code"] == "600036"
    assert artifact["result"]["records"][0]["name"].startswith("PINGAN")


def test_replay_stocks_call_chain_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh_full_market",
        "api": "stocks",
        "comparator": "table_exact",
        "call": {"kwargs": {"market": 1}},
    }
    case_dir = tmp_path / "corpus" / "stocks" / "sh_full_market"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh_full_market",
            "api": "stocks",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_stock_count",
                    "parser": "GetSecurityCountCmd",
                    "kwargs": {"market": 1},
                    "request_path": "steps/01_stock_count/request.bin",
                    "response_body_path": "steps/01_stock_count/response.body.bin",
                },
                {
                    "id": "02_list_00000",
                    "parser": "GetSecurityList",
                    "kwargs": {"market": 1, "start": 0},
                    "request_path": "steps/02_list_00000/request.bin",
                    "response_body_path": "steps/02_list_00000/response.body.bin",
                },
            ],
            "expected_path": "expected.json",
        },
    )
    _write_bytes(case_dir / "steps" / "01_stock_count" / "request.bin", b"req1")
    _write_bytes(case_dir / "steps" / "01_stock_count" / "response.body.bin", struct.pack("<H", 1))

    list_body = bytearray(struct.pack("<H", 1))
    list_body.extend(
        struct.pack(
            "<6sH8s4sBI4s",
            b"600036",
            100,
            "PINGAN".encode("gbk").ljust(8, b"\x00"),
            b"\x00\x00\x00\x00",
            2,
            1234,
            b"\x00\x00\x00\x00",
        )
    )
    _write_bytes(case_dir / "steps" / "02_list_00000" / "request.bin", b"req2")
    _write_bytes(case_dir / "steps" / "02_list_00000" / "response.body.bin", bytes(list_body))

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["code"] == "600036"
    assert artifact["result"]["records"][0]["name"].startswith("PINGAN")


def test_replay_quotes_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "single_sh",
        "api": "quotes",
        "comparator": "quotes_snapshot_exact",
        "call": {"kwargs": {"symbol": "600036"}},
    }
    case_dir = tmp_path / "corpus" / "quotes" / "single_sh"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "single_sh",
            "api": "quotes",
            "comparator": "quotes_snapshot_exact",
            "steps": [
                {
                    "id": "01_quotes",
                    "parser": "GetSecurityQuotesCmd",
                    "kwargs": {"all_stock": [[1, "600036"]]},
                    "request_path": "steps/01_quotes/request.bin",
                    "response_body_path": "steps/01_quotes/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/quotes/single_sh")
    _write_bytes(case_dir / "steps" / "01_quotes" / "request.bin", (source_case / "steps/01_quotes/request.bin").read_bytes())
    _write_bytes(
        case_dir / "steps" / "01_quotes" / "response.body.bin",
        (source_case / "steps/01_quotes/response.body.bin").read_bytes(),
    )

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["code"] == "600036"


def test_replay_bars_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "daily_sh_600036_last10",
        "api": "bars",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "600036", "frequency": "day", "start": 0, "offset": 10}},
    }
    case_dir = tmp_path / "corpus" / "bars" / "daily_sh_600036_last10"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "daily_sh_600036_last10",
            "api": "bars",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_bars",
                    "parser": "GetSecurityBarsCmd",
                    "kwargs": {
                        "symbol": "600036",
                        "market": 1,
                        "frequency": 9,
                        "start": 0,
                        "offset": 10,
                    },
                    "request_path": "steps/01_bars/request.bin",
                    "response_body_path": "steps/01_bars/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/bars/daily_sh_600036_last10/steps/01_bars")
    _write_bytes(case_dir / "steps/01_bars/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(case_dir / "steps/01_bars/response.body.bin", (source_case / "response.body.bin").read_bytes())

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert len(artifact["result"]["records"]) == 10
    assert {"open", "close", "datetime", "vol", "volume"} <= set(artifact["result"]["records"][0])


def test_replay_minutes_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "history_sh_000001_20171010",
        "api": "minutes",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "000001", "date": "20171010"}},
    }
    case_dir = tmp_path / "corpus" / "minutes" / "history_sh_000001_20171010"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "history_sh_000001_20171010",
            "api": "minutes",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_minutes",
                    "parser": "GetHistoryMinuteTimeData",
                    "kwargs": {"symbol": "000001", "market": 0, "date": 20171010},
                    "request_path": "steps/01_minutes/request.bin",
                    "response_body_path": "steps/01_minutes/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/minutes/history_sh_000001_20171010/steps/01_minutes")
    _write_bytes(case_dir / "steps/01_minutes/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(case_dir / "steps/01_minutes/response.body.bin", (source_case / "response.body.bin").read_bytes())

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert len(artifact["result"]["records"]) == 240
    assert {"price", "vol", "volume"} <= set(artifact["result"]["records"][0])


def test_replay_transaction_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "live_sh_600036_last10",
        "api": "transaction",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "600036", "start": 0, "offset": 10}},
    }
    case_dir = tmp_path / "corpus" / "transaction" / "live_sh_600036_last10"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "live_sh_600036_last10",
            "api": "transaction",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_transaction",
                    "parser": "GetTransactionData",
                    "kwargs": {"symbol": "600036", "market": 1, "start": 0, "offset": 10},
                    "request_path": "steps/01_transaction/request.bin",
                    "response_body_path": "steps/01_transaction/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/transaction/live_sh_600036_last10/steps/01_transaction")
    _write_bytes(case_dir / "steps/01_transaction/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(case_dir / "steps/01_transaction/response.body.bin", (source_case / "response.body.bin").read_bytes())

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert len(artifact["result"]["records"]) == 10
    assert {"time", "price", "vol", "num", "buyorsell", "volume"} <= set(artifact["result"]["records"][0])


def test_replay_history_transactions_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "history_sh_600036_20170209_last10",
        "api": "transactions",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "600036", "date": "20170209", "start": 0, "offset": 10}},
    }
    case_dir = tmp_path / "corpus" / "transactions" / "history_sh_600036_20170209_last10"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "history_sh_600036_20170209_last10",
            "api": "transactions",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_transactions",
                    "parser": "GetHistoryTransactionData",
                    "kwargs": {"symbol": "600036", "market": 1, "start": 0, "offset": 10, "date": 20170209},
                    "request_path": "steps/01_transactions/request.bin",
                    "response_body_path": "steps/01_transactions/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/transactions/history_sh_600036_20170209_last10/steps/01_transactions")
    _write_bytes(case_dir / "steps/01_transactions/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(
        case_dir / "steps/01_transactions/response.body.bin",
        (source_case / "response.body.bin").read_bytes(),
    )

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert len(artifact["result"]["records"]) == 10
    assert {"time", "price", "vol", "buyorsell", "volume"} <= set(artifact["result"]["records"][0])


def test_replay_finance_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sz_000001",
        "api": "finance",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "000001"}},
    }
    case_dir = tmp_path / "corpus" / "finance" / "sz_000001"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sz_000001",
            "api": "finance",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_finance",
                    "parser": "GetFinanceInfo",
                    "kwargs": {"symbol": "000001", "market": 0},
                    "request_path": "steps/01_finance/request.bin",
                    "response_body_path": "steps/01_finance/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/finance/sz_000001/steps/01_finance")
    _write_bytes(case_dir / "steps/01_finance/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(case_dir / "steps/01_finance/response.body.bin", (source_case / "response.body.bin").read_bytes())

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["code"] == "000001"


def test_replay_xdxr_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh_600036",
        "api": "xdxr",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "600036"}},
    }
    case_dir = tmp_path / "corpus" / "xdxr" / "sh_600036"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh_600036",
            "api": "xdxr",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_xdxr",
                    "parser": "GetXdXrInfo",
                    "kwargs": {"symbol": "600036", "market": 1},
                    "request_path": "steps/01_xdxr/request.bin",
                    "response_body_path": "steps/01_xdxr/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/xdxr/sh_600036/steps/01_xdxr")
    _write_bytes(case_dir / "steps/01_xdxr/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(case_dir / "steps/01_xdxr/response.body.bin", (source_case / "response.body.bin").read_bytes())

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["name"]


def test_replay_f10_categories_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh_600036",
        "api": "f10_categories",
        "comparator": "table_exact",
        "call": {"kwargs": {"symbol": "600036"}},
    }
    case_dir = tmp_path / "corpus" / "f10_categories" / "sh_600036"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh_600036",
            "api": "f10_categories",
            "comparator": "table_exact",
            "steps": [
                {
                    "id": "01_f10_categories",
                    "parser": "GetCompanyInfoCategory",
                    "kwargs": {"symbol": "600036", "market": 1},
                    "request_path": "steps/01_f10_categories/request.bin",
                    "response_body_path": "steps/01_f10_categories/response.body.bin",
                }
            ],
            "expected_path": "expected.json",
        },
    )
    source_case = Path("compat/corpus/f10_categories/sh_600036/steps/01_f10_categories")
    _write_bytes(case_dir / "steps/01_f10_categories/request.bin", (source_case / "request.bin").read_bytes())
    _write_bytes(
        case_dir / "steps/01_f10_categories/response.body.bin",
        (source_case / "response.body.bin").read_bytes(),
    )

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "dataframe"
    assert artifact["result"]["records"][0]["name"] == "最新提示"


def test_replay_f10_content_case_from_corpus_with_next_runtime(tmp_path: Path) -> None:
    spec = {
        "case_id": "sh_600036__latest_tip",
        "api": "f10_content",
        "comparator": "scalar_exact",
        "call": {"kwargs": {"symbol": "600036", "name": "最新提示"}},
    }
    case_dir = tmp_path / "corpus" / "f10_content" / "sh_600036__latest_tip"
    write_json(
        case_dir / "manifest.json",
        {
            "case_id": "sh_600036__latest_tip",
            "api": "f10_content",
            "comparator": "scalar_exact",
            "steps": [
                {
                    "id": "01_f10_categories",
                    "parser": "GetCompanyInfoCategory",
                    "kwargs": {"symbol": "600036", "market": 1},
                    "request_path": "steps/01_f10_categories/request.bin",
                    "response_body_path": "steps/01_f10_categories/response.body.bin",
                },
                {
                    "id": "02_f10_content",
                    "parser": "GetCompanyInfoContent",
                    "kwargs": {
                        "symbol": "600036",
                        "market": 1,
                        "name": "最新提示",
                        "filename": "600036.txt",
                        "start": 0,
                        "length": 15257,
                    },
                    "request_path": "steps/02_f10_content/request.bin",
                    "response_body_path": "steps/02_f10_content/response.body.bin",
                },
            ],
            "expected_path": "expected.json",
        },
    )
    base = Path("compat/corpus/f10_content/sh_600036__latest_tip/steps")
    _write_bytes(case_dir / "steps/01_f10_categories/request.bin", (base / "01_f10_categories/request.bin").read_bytes())
    _write_bytes(
        case_dir / "steps/01_f10_categories/response.body.bin",
        (base / "01_f10_categories/response.body.bin").read_bytes(),
    )
    _write_bytes(case_dir / "steps/02_f10_content/request.bin", (base / "02_f10_content/request.bin").read_bytes())
    _write_bytes(
        case_dir / "steps/02_f10_content/response.body.bin",
        (base / "02_f10_content/response.body.bin").read_bytes(),
    )

    artifact = replay_case(spec, tmp_path / "corpus", runtime="next")

    assert artifact["status"] == "ok"
    assert artifact["result_type"] == "str"
    assert artifact["result"].startswith("最新提示")


def test_build_live_decode_artifact_pair_uses_same_shape(monkeypatch) -> None:
    spec = {
        "case_id": "single_sh",
        "api": "quotes",
        "comparator": "quotes_snapshot_exact",
        "client": {"kind": "quotes", "factory": {"server": ["127.0.0.1", 7709]}},
        "call": {"kwargs": {"symbol": "600036"}},
    }
    source_case = Path("compat/corpus/quotes/single_sh/steps/01_quotes")
    fake_step = type(
        "FakeStep",
        (),
        {
            "step_id": "01_quotes",
            "parser": "GetSecurityQuotesCmd",
            "kwargs": {"all_stock": [[1, "600036"]]},
            "request": (source_case / "request.bin").read_bytes(),
            "response_body": (source_case / "response.body.bin").read_bytes(),
            "parsed": None,
        },
    )

    def fake_build_client(spec, runtime="legacy"):
        return object()

    def fake_capture_case(client, spec):
        expected = load_json(Path("compat/corpus/quotes/single_sh/expected.json"))
        return [fake_step], expected["result"]

    monkeypatch.setattr("compat.registry._build_client", fake_build_client)
    monkeypatch.setattr("compat.registry._capture_case", fake_capture_case)
    monkeypatch.setattr("compat.registry._close_client", lambda client: None)

    legacy, next_artifact = build_live_decode_artifact_pair(spec)

    assert legacy["status"] == "ok"
    assert next_artifact["status"] == "ok"
    assert next_artifact["result_type"] == "dataframe"
    assert next_artifact["result"]["records"][0]["code"] == "600036"
