from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import mootdx_next.api.pandas as pandas_module
from compat.common import build_artifact
from compat.common import load_json
from compat.comparators import compare_payloads
from mootdx_next import PandasClient
from mootdx_next import StdQuoteProtocol

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "compat" / "corpus"


def _baseline(api: str, case_id: str) -> dict:
    return load_json(CORPUS / api / case_id / "expected.json")


def _frame_from_baseline(api: str, case_id: str) -> pd.DataFrame:
    result = _baseline(api, case_id)["result"]
    return pd.DataFrame.from_records(result["records"], columns=result["columns"])


def _assert_exact_artifact(api: str, case_id: str, actual, expected) -> None:
    left = build_artifact(
        api=api,
        case_id=case_id,
        comparator="table_exact",
        result=expected,
    )
    right = build_artifact(
        api=api,
        case_id=case_id,
        comparator="table_exact",
        result=actual,
    )
    assert compare_payloads(left, right, "table_exact") == []


class WrapperRaw:
    closed = False

    def __init__(self) -> None:
        self.minute_dates: list[str] = []
        self.sh_stocks = _frame_from_baseline("stocks", "sh_full_market").to_dict("records")
        self.sz_stocks = _frame_from_baseline("stocks", "sz_full_market").to_dict("records")
        self.minutes_rows = _frame_from_baseline("minutes", "history_sz_000001_20171010").to_dict("records")
        self.categories = _baseline("f10_categories", "sh_600036")["result"]["records"]
        self.content = _baseline("f10_content", "sh_600036__latest_tip")["result"]
        self.index_rows = _frame_from_baseline("index", "sh_000001_daily_last10").to_dict("records")

    def stocks(self, market: int):
        return self.sz_stocks if market == 0 else self.sh_stocks

    def minutes(self, symbol: str, date: str):
        self.minute_dates.append(str(date))
        return self.minutes_rows

    def f10_categories(self, symbol: str):
        return self.categories

    def f10_content(self, symbol: str, name: str):
        assert name == "最新提示"
        return self.content

    def index_bars(self, symbol: str, frequency=9, start=0, offset=800, market=None):
        return self.index_rows

    def xdxr(self, symbol: str):
        return []

    def close(self) -> None:
        self.closed = True


def test_stock_all_matches_composed_legacy_market_artifacts() -> None:
    raw = WrapperRaw()
    client = PandasClient(raw_client=raw)
    expected = pd.concat(
        [
            _frame_from_baseline("stocks", "sz_full_market"),
            _frame_from_baseline("stocks", "sh_full_market"),
        ],
        ignore_index=True,
    )

    _assert_exact_artifact("stock_all", "sz_then_sh", client.stock_all(), expected)


def test_minute_wrapper_matches_legacy_minutes_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDateTime:
        @classmethod
        def now(cls):
            return datetime(2017, 10, 10, 10, 0)

    monkeypatch.setattr(pandas_module, "datetime", FrozenDateTime)
    raw = WrapperRaw()
    client = PandasClient(raw_client=raw)
    expected = _frame_from_baseline("minutes", "history_sz_000001_20171010")

    _assert_exact_artifact("minute", "sz_000001_20171010", client.minute("000001"), expected)
    assert raw.minute_dates == ["20171010"]


def test_f10_wrappers_match_legacy_category_and_content_artifacts() -> None:
    raw = WrapperRaw()
    client = PandasClient(raw_client=raw)

    _assert_exact_artifact("F10C", "sh_600036", client.F10C("600036"), raw.categories)
    _assert_exact_artifact("F10", "sh_600036__latest_tip", client.F10("600036", "最新提示"), raw.content)


def test_index_wrapper_matches_legacy_index_artifact() -> None:
    raw = WrapperRaw()
    client = PandasClient(raw_client=raw)
    expected = _frame_from_baseline("index", "sh_000001_daily_last10")

    _assert_exact_artifact(
        "index",
        "sh_000001_daily_last10",
        client.index("000001", frequency=9, start=0, offset=10, market=1),
        expected,
    )


class HistoryWrapperRaw:
    closed = False

    def __init__(self) -> None:
        case_dir = CORPUS / "get_k_data" / "sh_600036_20260720_20260725_populated"
        manifest = load_json(case_dir / "manifest.json")
        step = manifest["steps"][0]
        body = (case_dir / step["response_body_path"]).read_bytes()
        self.rows = StdQuoteProtocol().decode_bars(body, frequency=9)

    def bars(self, symbol: str, frequency=9, start=0, offset=800):
        return self.rows if start == 0 else []

    def xdxr(self, symbol: str):
        return []

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("api", "kwargs"),
    [
        (
            "get_k_data",
            {"code": "600036", "start_date": "2026-07-20", "end_date": "2026-07-25"},
        ),
        (
            "k",
            {"symbol": "600036", "begin": "2026-07-20", "end": "2026-07-25"},
        ),
        (
            "ohlc",
            {"symbol": "600036", "begin": "2026-07-20", "end": "2026-07-25"},
        ),
    ],
)
def test_history_wrappers_match_populated_legacy_artifacts(api: str, kwargs: dict[str, str]) -> None:
    case_id = "sh_600036_20260720_20260725_populated"
    client = PandasClient(raw_client=HistoryWrapperRaw())
    expected = _frame_from_baseline(api, case_id)

    _assert_exact_artifact(api, case_id, getattr(client, api)(**kwargs), expected)
