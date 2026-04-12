from __future__ import annotations

import pandas as pd


def stocks_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def quotes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)

    result = pd.DataFrame(data=rows)
    if "vol" in result.columns:
        result["volume"] = result.vol
    return result


def bars_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def minutes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def transaction_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def transactions_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def finance_to_frame(row: dict[str, object] | None) -> pd.DataFrame:
    if not row:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=[row])


def xdxr_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def f10_categories_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)


def block_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(data=None)
    return pd.DataFrame(data=rows)
