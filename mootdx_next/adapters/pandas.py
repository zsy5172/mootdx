from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


def _empty_frame(columns: list[str] | tuple[str, ...] | None = None) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _frame_from_records(
    rows: list[dict[str, object]],
    *,
    columns: list[str] | tuple[str, ...] | None = None,
    datetime_column: str | None = None,
    date_column: str | None = None,
    add_volume_alias: bool = False,
) -> pd.DataFrame:
    if not rows:
        return _empty_frame(columns)

    frame = pd.DataFrame.from_records(rows, columns=columns)

    if add_volume_alias and "vol" in frame.columns and "volume" not in frame.columns:
        frame["volume"] = frame["vol"].to_numpy(copy=False)

    if datetime_column and datetime_column in frame.columns:
        frame.index = pd.to_datetime(frame[datetime_column])
    elif date_column and date_column in frame.columns:
        frame.index = pd.to_datetime(frame[date_column])

    return frame


def stocks_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows)


def quotes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, add_volume_alias=True)


def limit_prices_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, columns=["market", "code", "limit_up", "limit_down"])


def price_limit_to_frame(row: dict[str, object] | None) -> pd.DataFrame:
    if not row:
        return _empty_frame(["market", "code", "limit_up", "limit_down", "source"])
    return pd.DataFrame.from_records(
        [row],
        columns=["market", "code", "limit_up", "limit_down", "source"],
    )


def bars_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, datetime_column="datetime")


def call_auction_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = _frame_from_records(
        rows,
        columns=[
            "time",
            "hour",
            "minute",
            "second",
            "price",
            "matched",
            "unmatched_signed",
            "unmatched",
            "side",
            "side_name",
        ],
    )
    if "time" in frame.columns:
        frame.index = pd.Index(frame["time"], name="time")
    return frame


def minutes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, date_column="date", add_volume_alias=True)


def transaction_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, add_volume_alias=True)


def transactions_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, add_volume_alias=True)


def finance_to_frame(row: dict[str, object] | None) -> pd.DataFrame:
    if not row:
        return _empty_frame()
    return pd.DataFrame.from_records([row])


def xdxr_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows)


def xdxr_by_date_to_frame(
    rows_by_date: Mapping[str, list[dict[str, object]]],
) -> pd.DataFrame:
    rows = [
        dict(row, event_date=date)
        for date, events in rows_by_date.items()
        for row in events
    ]
    if not rows:
        frame = _empty_frame(["event_date"])
        frame.index = pd.DatetimeIndex([], name="event_date")
        return frame
    return _frame_from_records(rows, date_column="event_date")


def f10_categories_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows)


def block_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows)


def ex_markets_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, columns=["market", "category", "name", "short_name"])


def ex_instruments_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(
        rows,
        columns=["start", "category", "market", "code", "name", "description"],
    )


def ex_quote_to_frame(row: dict[str, object] | None) -> pd.DataFrame:
    if not row:
        return _empty_frame()
    return pd.DataFrame.from_records([row])


def ex_quotes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows)


def ex_bars_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return _frame_from_records(rows, datetime_column="datetime")


def ex_minutes_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = _frame_from_records(rows)
    if "time" in frame.columns:
        frame.index = pd.Index(frame["time"], name="time")
    return frame


def ex_transactions_to_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = _frame_from_records(rows, datetime_column="datetime")
    if "datetime" not in frame.columns and "time" in frame.columns:
        frame.index = pd.Index(frame["time"], name="time")
    return frame
