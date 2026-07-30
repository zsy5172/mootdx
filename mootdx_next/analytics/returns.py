from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd


def forward_returns(
    values: pd.DataFrame | pd.Series,
    horizons: Iterable[int] = (1, 5, 10, 20),
    *,
    price: str = "close",
    group_by: str | None = None,
    percent: bool = False,
) -> pd.DataFrame:
    """Calculate future returns by ordered trading rows, not calendar days.

    Results are aligned to the source row. For example, ``return_5`` compares
    the current close with the close five records later. Trailing rows without
    a future observation remain ``NaN``.
    """

    periods = _horizons(horizons)
    frame, prices = _input(values, price, group_by)
    scale = 100.0 if percent else 1.0
    output: dict[str, pd.Series] = {}
    for horizon in periods:
        if group_by is None:
            future = prices.shift(-horizon)
        else:
            future = prices.groupby(frame[group_by], sort=False, dropna=False).shift(-horizon)
        result = (future / prices - 1.0) * scale
        result.name = f"return_{horizon}"
        output[result.name] = result
    return pd.DataFrame(output, index=prices.index)


def _input(
    values: pd.DataFrame | pd.Series,
    price: str,
    group_by: str | None,
) -> tuple[pd.DataFrame, pd.Series]:
    if isinstance(values, pd.Series):
        if group_by is not None:
            raise ValueError("group_by requires a DataFrame input")
        frame = values.to_frame(name=price)
        raw = values
    elif isinstance(values, pd.DataFrame):
        frame = values.copy(deep=True)
        if price not in frame.columns:
            raise ValueError(f"frame is missing required column: {price}")
        if group_by is not None and group_by not in frame.columns:
            raise ValueError(f"frame is missing group column: {group_by}")
        raw = frame[price]
    else:
        raise TypeError("forward_returns requires a pandas Series or DataFrame")

    try:
        prices = pd.to_numeric(raw.copy(deep=True), errors="raise").astype("float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{price} must contain numeric values") from exc
    finite = prices.dropna().map(math.isfinite)
    if not finite.all() or (prices.dropna() <= 0).any():
        raise ValueError(f"{price} must contain positive finite values or NaN")
    return frame, prices


def _horizons(values: Iterable[int]) -> tuple[int, ...]:
    try:
        result = tuple(values)
    except TypeError as exc:
        raise TypeError("horizons must be an iterable of positive integers") from exc
    if not result:
        raise ValueError("horizons cannot be empty")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in result):
        raise ValueError("horizons must contain positive integers")
    if len(set(result)) != len(result):
        raise ValueError("horizons cannot contain duplicates")
    return result


__all__ = ["forward_returns"]
