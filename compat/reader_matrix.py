from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from compat.common import build_artifact
from compat.common import normalize_value


@dataclass(frozen=True, slots=True)
class ReaderCase:
    case_id: str
    market: str
    method: str
    kwargs: dict[str, Any]


READER_CASES = (
    ReaderCase("std_daily_sh_bond", "std", "daily", {"symbol": "127021"}),
    ReaderCase("std_daily_sz_equity", "std", "daily", {"symbol": "000001"}),
    ReaderCase("std_daily_custom_index", "std", "daily", {"symbol": "881478"}),
    ReaderCase("std_minute_lc1", "std", "minute", {"symbol": "688001", "suffix": "1"}),
    ReaderCase("std_minute_lc5", "std", "minute", {"symbol": "688001", "suffix": "5"}),
    ReaderCase("ext_daily", "ext", "daily", {"symbol": "4#CF7D0LAO"}),
    ReaderCase("ext_minute_missing", "ext", "minute", {"symbol": "4#CF7D0LAO"}),
    ReaderCase("std_block_gn", "std", "block", {"symbol": "block_gn.dat"}),
)


def capture_reader_case(
    case: ReaderCase,
    *,
    runtime: str,
    tdxdir: str | Path,
) -> dict[str, Any]:
    if runtime == "legacy":
        from mootdx.reader import Reader
    elif runtime == "next":
        from mootdx_next.reader import Reader
    else:
        raise ValueError(f"unsupported reader runtime: {runtime}")

    try:
        reader = Reader.factory(market=case.market, tdxdir=str(tdxdir))
        result = getattr(reader, case.method)(**case.kwargs)
        artifact = build_artifact(
            case_id=case.case_id,
            api=f"reader.{case.method}",
            comparator="scalar_exact",
            result=_reader_result(result),
        )
    except Exception as exc:
        artifact = build_artifact(
            case_id=case.case_id,
            api=f"reader.{case.method}",
            comparator="scalar_exact",
            error=exc,
        )
    artifact["reader_case"] = {
        "market": case.market,
        "method": case.method,
        "kwargs": normalize_value(case.kwargs),
    }
    return artifact


def _reader_result(result: Any) -> Any:
    if not isinstance(result, pd.DataFrame):
        return normalize_value(result)
    payload = {
        "columns": list(result.columns),
        "index_name": result.index.name,
        "index_type": type(result.index).__name__,
        "index": [normalize_value(value) for value in result.index],
        "records": [normalize_value(row) for row in result.to_dict(orient="records")],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "columns": payload["columns"],
        "index_name": payload["index_name"],
        "index_type": payload["index_type"],
        "row_count": len(result),
        "first_index": payload["index"][:3],
        "last_index": payload["index"][-3:],
        "first_records": payload["records"][:3],
        "last_records": payload["records"][-3:],
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }
