from __future__ import annotations

import json
from datetime import date
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_value(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {
            "columns": list(value.columns),
            "records": [normalize_value(row) for row in value.to_dict(orient="records")],
        }

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]

    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    if isinstance(value, Path):
        return str(value)

    return value


def result_type(value: Any) -> str:
    if isinstance(value, pd.DataFrame):
        return "dataframe"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def build_artifact(
    *,
    case_id: str,
    api: str,
    comparator: str,
    result: Any = None,
    error: Exception | dict[str, Any] | None = None,
) -> dict[str, Any]:
    if error is None:
        return {
            "case_id": case_id,
            "api": api,
            "comparator": comparator,
            "status": "ok",
            "result_type": result_type(result),
            "result": normalize_value(result),
            "error": None,
        }

    if isinstance(error, Exception):
        error_payload = {
            "type": type(error).__name__,
            "message": str(error),
        }
    else:
        error_payload = error

    return {
        "case_id": case_id,
        "api": api,
        "comparator": comparator,
        "status": "error",
        "result_type": None,
        "result": None,
        "error": error_payload,
    }
