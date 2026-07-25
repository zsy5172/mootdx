from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compat.common import write_json

DEFAULT_SERVER = ("110.41.174.169", 7709)
F10_SERVER = ("175.178.128.227", 7709)


@dataclass(frozen=True, slots=True)
class MatrixCase:
    api: str
    case_id: str
    comparator: str
    kwargs: dict[str, Any]
    dimensions: dict[str, Any]
    tags: tuple[str, ...] = ()
    server: tuple[str, int] = DEFAULT_SERVER

    def spec_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "api": self.api,
            "comparator": self.comparator,
            "client": {
                "kind": "quotes",
                "factory": {
                    "market": "std",
                    "timeout": 5,
                    "raise_exception": True,
                    "server": [self.server[0], self.server[1]],
                },
            },
            "call": {"kwargs": self.kwargs},
            "matrix": {
                "dimensions": self.dimensions,
                "tags": list(self.tags),
            },
        }


MATRIX_CASES: tuple[MatrixCase, ...] = (
    MatrixCase(
        api="stock_count",
        case_id="sh",
        comparator="scalar_exact",
        kwargs={"market": 1},
        dimensions={"market": "sh"},
        tags=("systematic", "scalar"),
    ),
    MatrixCase(
        api="stock_count",
        case_id="sz",
        comparator="scalar_exact",
        kwargs={"market": 0},
        dimensions={"market": "sz"},
        tags=("systematic", "scalar"),
    ),
    MatrixCase(
        api="stocks",
        case_id="sh_full_market",
        comparator="table_exact",
        kwargs={"market": 1},
        dimensions={"market": "sh", "result_shape": "full_market"},
        tags=("systematic", "large"),
    ),
    MatrixCase(
        api="stocks",
        case_id="sz_full_market",
        comparator="table_exact",
        kwargs={"market": 0},
        dimensions={"market": "sz", "result_shape": "full_market"},
        tags=("systematic", "large"),
    ),
    MatrixCase(
        api="quotes",
        case_id="single_sh",
        comparator="quotes_snapshot_exact",
        kwargs={"symbol": "600036"},
        dimensions={"batch_size": 1, "market_mix": "sh_only", "duplicates": False},
        tags=("systematic", "snapshot"),
    ),
    MatrixCase(
        api="quotes",
        case_id="single_sz",
        comparator="quotes_snapshot_exact",
        kwargs={"symbol": "000001"},
        dimensions={"batch_size": 1, "market_mix": "sz_only", "duplicates": False},
        tags=("systematic", "snapshot"),
    ),
    MatrixCase(
        api="quotes",
        case_id="mixed_batch",
        comparator="quotes_snapshot_exact",
        kwargs={"symbol": ["600036", "000001"]},
        dimensions={"batch_size": 2, "market_mix": "mixed_sh_sz", "duplicates": False},
        tags=("systematic", "snapshot"),
    ),
    MatrixCase(
        api="quotes",
        case_id="duplicated_batch",
        comparator="quotes_snapshot_exact",
        kwargs={"symbol": ["600036", "600036", "000001"]},
        dimensions={"batch_size": 3, "market_mix": "mixed_sh_sz", "duplicates": True},
        tags=("systematic", "snapshot"),
    ),
    MatrixCase(
        api="bars",
        case_id="daily_sh_600036_last10",
        comparator="table_exact",
        kwargs={"symbol": "600036", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "sh", "frequency": "daily", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="bars",
        case_id="daily_sh_600036_last60_adjustment_window",
        comparator="table_exact",
        kwargs={"symbol": "600036", "frequency": 9, "start": 0, "offset": 60},
        dimensions={"market": "sh", "frequency": "daily", "window": "adjustment_event"},
        tags=("systematic", "history", "adjustment"),
    ),
    MatrixCase(
        api="bars",
        case_id="daily_sh_510500_last60_adjustment_window",
        comparator="table_exact",
        kwargs={"symbol": "510500", "frequency": 9, "start": 0, "offset": 60},
        dimensions={"market": "sh", "frequency": "daily", "window": "etf_adjustment_event"},
        tags=("systematic", "history", "adjustment", "etf"),
    ),
    MatrixCase(
        api="bars",
        case_id="daily_sz_000001_last10",
        comparator="table_exact",
        kwargs={"symbol": "000001", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "sz", "frequency": "daily", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="bars",
        case_id="daily_bj_430090_last10",
        comparator="table_exact",
        kwargs={"symbol": "430090", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "bj", "frequency": "daily", "window": "head"},
        tags=("systematic", "history", "empty_ok"),
    ),
    MatrixCase(
        api="bars",
        case_id="intraday_5m_sh_600036_last20",
        comparator="table_exact",
        kwargs={"symbol": "600036", "frequency": "5m", "start": 0, "offset": 20},
        dimensions={"market": "sh", "frequency": "5m", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="bars",
        case_id="weekly_sh_600036_last12",
        comparator="table_exact",
        kwargs={"symbol": "600036", "frequency": 5, "start": 0, "offset": 12},
        dimensions={"market": "sh", "frequency": "weekly", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="bars",
        case_id="monthly_sh_600036_last12",
        comparator="table_exact",
        kwargs={"symbol": "600036", "frequency": 6, "start": 0, "offset": 12},
        dimensions={"market": "sh", "frequency": "monthly", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="minutes",
        case_id="history_sh_000001_20171010",
        comparator="table_exact",
        kwargs={"symbol": "000001", "date": "20171010"},
        dimensions={"market": "sh", "result_shape": "populated"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="minutes",
        case_id="history_sz_000001_20171010",
        comparator="table_exact",
        kwargs={"symbol": "000001", "date": "20171010", "market": "sz"},
        dimensions={"market": "sz", "result_shape": "populated"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="minutes",
        case_id="history_empty_sz_159995_20200130",
        comparator="table_exact",
        kwargs={"symbol": "159995", "date": "20200130"},
        dimensions={"market": "sz", "result_shape": "empty"},
        tags=("systematic", "history", "empty_ok"),
    ),
    MatrixCase(
        api="transaction",
        case_id="live_sh_600036_last10",
        comparator="table_exact",
        kwargs={"symbol": "600036", "start": 0, "offset": 10},
        dimensions={"market": "sh", "window": "head"},
        tags=("systematic", "realtime"),
    ),
    MatrixCase(
        api="transaction",
        case_id="live_sz_000001_last10",
        comparator="table_exact",
        kwargs={"symbol": "000001", "start": 0, "offset": 10},
        dimensions={"market": "sz", "window": "head"},
        tags=("systematic", "realtime"),
    ),
    MatrixCase(
        api="transactions",
        case_id="history_sh_600036_20170209_last10",
        comparator="table_exact",
        kwargs={"symbol": "600036", "start": 0, "offset": 10, "date": "20170209"},
        dimensions={"market": "sh", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="transactions",
        case_id="history_sz_000001_20171010_last10",
        comparator="table_exact",
        kwargs={"symbol": "000001", "start": 0, "offset": 10, "date": "20171010"},
        dimensions={"market": "sz", "window": "head"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="transactions",
        case_id="history_sh_600036_20170209_start20_offset15",
        comparator="table_exact",
        kwargs={"symbol": "600036", "start": 20, "offset": 15, "date": "20170209"},
        dimensions={"market": "sh", "window": "mid"},
        tags=("systematic", "history"),
    ),
    MatrixCase(
        api="finance",
        case_id="sz_000001",
        comparator="table_exact",
        kwargs={"symbol": "000001"},
        dimensions={"market": "sz"},
        tags=("systematic", "info"),
    ),
    MatrixCase(
        api="finance",
        case_id="sh_600036",
        comparator="table_exact",
        kwargs={"symbol": "600036"},
        dimensions={"market": "sh"},
        tags=("systematic", "info"),
    ),
    MatrixCase(
        api="xdxr",
        case_id="sh_600036",
        comparator="table_exact",
        kwargs={"symbol": "600036"},
        dimensions={"market": "sh"},
        tags=("systematic", "info"),
    ),
    MatrixCase(
        api="xdxr",
        case_id="sh_510500",
        comparator="table_exact",
        kwargs={"symbol": "510500"},
        dimensions={"market": "sh", "security_type": "etf"},
        tags=("systematic", "info", "adjustment", "etf"),
    ),
    MatrixCase(
        api="xdxr",
        case_id="sz_000001",
        comparator="table_exact",
        kwargs={"symbol": "000001"},
        dimensions={"market": "sz"},
        tags=("systematic", "info"),
    ),
    MatrixCase(
        api="f10_categories",
        case_id="sh_600036",
        comparator="table_exact",
        kwargs={"symbol": "600036"},
        dimensions={"market": "sh", "result_shape": "populated"},
        tags=("systematic", "info"),
        server=F10_SERVER,
    ),
    MatrixCase(
        api="f10_categories",
        case_id="sz_000001_empty",
        comparator="table_exact",
        kwargs={"symbol": "000001"},
        dimensions={"market": "sz", "result_shape": "empty"},
        tags=("systematic", "info", "empty_ok"),
        server=F10_SERVER,
    ),
    MatrixCase(
        api="f10_content",
        case_id="sh_600036__latest_tip",
        comparator="scalar_exact",
        kwargs={"symbol": "600036", "name": "最新提示"},
        dimensions={"market": "sh", "category": "latest_tip"},
        tags=("systematic", "info"),
        server=F10_SERVER,
    ),
    MatrixCase(
        api="index_bars",
        case_id="sh_000001_daily_last10",
        comparator="table_exact",
        kwargs={"symbol": "000001", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "sh", "frequency": "daily", "wrapper": "index_bars"},
        tags=("systematic", "compat"),
    ),
    MatrixCase(
        api="index_bars",
        case_id="sz_399001_daily_last10",
        comparator="table_exact",
        kwargs={"symbol": "399001", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "sz", "frequency": "daily", "wrapper": "index_bars"},
        tags=("systematic", "compat"),
    ),
    MatrixCase(
        api="index_bars",
        case_id="sh_000001_5m_last20",
        comparator="table_exact",
        kwargs={"symbol": "000001", "frequency": "5m", "start": 0, "offset": 20},
        dimensions={"market": "sh", "frequency": "5m", "wrapper": "index_bars"},
        tags=("systematic", "compat"),
    ),
    MatrixCase(
        api="index",
        case_id="sh_000001_daily_last10",
        comparator="table_exact",
        kwargs={"symbol": "000001", "frequency": 9, "start": 0, "offset": 10},
        dimensions={"market": "sh", "frequency": "daily", "wrapper": "index_alias"},
        tags=("systematic", "compat", "alias"),
    ),
    MatrixCase(
        api="block",
        case_id="default_block_dat",
        comparator="table_exact",
        kwargs={"tofile": "block.dat"},
        dimensions={"block_file": "block.dat"},
        tags=("systematic", "compat"),
    ),
    MatrixCase(
        api="block",
        case_id="block_zs_dat",
        comparator="table_exact",
        kwargs={"tofile": "block_zs.dat"},
        dimensions={"block_file": "block_zs.dat"},
        tags=("systematic", "compat"),
    ),
    MatrixCase(
        api="get_k_data",
        case_id="sh_600036_20190703_20190710_empty",
        comparator="table_exact",
        kwargs={"code": "600036", "start_date": "2019-07-03", "end_date": "2019-07-10"},
        dimensions={"market": "sh", "result_shape": "empty", "wrapper": "get_k_data"},
        tags=("systematic", "compat", "empty_ok"),
    ),
    MatrixCase(
        api="get_k_data",
        case_id="sh_600036_20260720_20260725_populated",
        comparator="table_exact",
        kwargs={"code": "600036", "start_date": "2026-07-20", "end_date": "2026-07-25"},
        dimensions={"market": "sh", "result_shape": "populated", "wrapper": "get_k_data"},
        tags=("systematic", "compat", "history"),
    ),
    MatrixCase(
        api="k",
        case_id="sh_600036_20190703_20190710_empty",
        comparator="table_exact",
        kwargs={"symbol": "600036", "begin": "2019-07-03", "end": "2019-07-10"},
        dimensions={"market": "sh", "result_shape": "empty", "wrapper": "k"},
        tags=("systematic", "compat", "empty_ok"),
    ),
    MatrixCase(
        api="k",
        case_id="sh_600036_20260720_20260725_populated",
        comparator="table_exact",
        kwargs={"symbol": "600036", "begin": "2026-07-20", "end": "2026-07-25"},
        dimensions={"market": "sh", "result_shape": "populated", "wrapper": "k"},
        tags=("systematic", "compat", "history"),
    ),
    MatrixCase(
        api="ohlc",
        case_id="sh_600036_20190703_20190710_empty",
        comparator="table_exact",
        kwargs={"symbol": "600036", "begin": "2019-07-03", "end": "2019-07-10"},
        dimensions={"market": "sh", "result_shape": "empty", "wrapper": "ohlc"},
        tags=("systematic", "compat", "empty_ok"),
    ),
    MatrixCase(
        api="ohlc",
        case_id="sh_600036_20260720_20260725_populated",
        comparator="table_exact",
        kwargs={"symbol": "600036", "begin": "2026-07-20", "end": "2026-07-25"},
        dimensions={"market": "sh", "result_shape": "populated", "wrapper": "ohlc"},
        tags=("systematic", "compat", "history"),
    ),
)


def iter_cases() -> tuple[MatrixCase, ...]:
    return MATRIX_CASES


def spec_paths(root: str | Path) -> list[Path]:
    base = Path(root)
    return [base / case.api / f"{case.case_id}.json" for case in MATRIX_CASES]


def write_specs(root: str | Path) -> list[Path]:
    written: list[Path] = []
    base = Path(root)
    for case in MATRIX_CASES:
        path = base / case.api / f"{case.case_id}.json"
        write_json(path, case.spec_payload())
        written.append(path)
    return written


def coverage_summary() -> dict[str, dict[str, list[Any]]]:
    summary: dict[str, dict[str, set[Any]]] = defaultdict(lambda: defaultdict(set))
    for case in MATRIX_CASES:
        for key, value in case.dimensions.items():
            summary[case.api][key].add(value)
    return {
        api: {dimension: sorted(values) for dimension, values in dimensions.items()}
        for api, dimensions in summary.items()
    }
