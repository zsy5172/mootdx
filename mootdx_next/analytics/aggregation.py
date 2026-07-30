from __future__ import annotations

import math
from collections.abc import Iterable
from collections.abc import Mapping
from datetime import date as Date
from datetime import datetime
from datetime import time as Time
from typing import Literal

import pandas as pd

Records = pd.DataFrame | Iterable[Mapping[str, object]]
Side = Literal["buy", "sell", "neutral"]

BAR_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vol",
    "amount",
    "datetime",
    "previous_close",
]


def summarize_trade_sides(
    trades: Records,
    *,
    lot_size: int = 100,
) -> dict[str, float | int]:
    """Summarize buy, sell and neutral transaction volume and amount.

    Standard TDX transaction volume is measured in lots. Amount is reused
    when present; otherwise it is derived as ``price * volume * lot_size``.
    The input is copied and never modified.
    """

    if isinstance(lot_size, bool) or int(lot_size) <= 0:
        raise ValueError("lot_size must be a positive integer")
    totals: dict[str, float | int] = {}
    for side in ("buy", "sell", "neutral"):
        totals[f"{side}_trade_count"] = 0
        totals[f"{side}_volume"] = 0.0
        totals[f"{side}_amount"] = 0.0

    for row in _records(trades):
        side = _trade_side(row)
        volume = _non_negative_number(_first(row, "volume", "vol"), "transaction volume")
        amount_value = row.get("amount")
        amount = (
            _non_negative_number(amount_value, "transaction amount")
            if not _is_missing(amount_value)
            else _number(row.get("price"), "transaction price") * volume * int(lot_size)
        )
        totals[f"{side}_trade_count"] += 1
        totals[f"{side}_volume"] += volume
        totals[f"{side}_amount"] += amount

    totals["total_trade_count"] = sum(
        int(totals[f"{side}_trade_count"]) for side in ("buy", "sell", "neutral")
    )
    totals["total_volume"] = sum(
        float(totals[f"{side}_volume"]) for side in ("buy", "sell", "neutral")
    )
    totals["total_amount"] = sum(
        float(totals[f"{side}_amount"]) for side in ("buy", "sell", "neutral")
    )
    return totals


def trades_to_minute_bars(
    trades: Records,
    *,
    date: str | int | Date | None = None,
    include_auction: bool = True,
    lot_size: int = 100,
    outside_session: Literal["raise", "drop"] = "raise",
) -> pd.DataFrame:
    """Aggregate standard-market transactions into A-share minute bars.

    Continuous-auction trades are labelled by interval end: a transaction
    stamped 09:30 belongs to the 09:31 bar. Transactions before 09:30 form a
    standalone 09:30 call-auction bar when ``include_auction`` is true.
    Morning and afternoon sessions are bucketed independently.
    """

    if isinstance(lot_size, bool) or int(lot_size) <= 0:
        raise ValueError("lot_size must be a positive integer")
    if outside_session not in {"raise", "drop"}:
        raise ValueError("outside_session must be 'raise' or 'drop'")

    normalized_date = _normalize_date(date) if date is not None else None
    values: list[tuple[datetime, int, Mapping[str, object]]] = []
    for position, row in enumerate(_records(trades)):
        values.append((_trade_datetime(row, normalized_date), position, row))
    values.sort(key=lambda item: (item[0], item[1]))

    groups: dict[datetime, list[Mapping[str, object]]] = {}
    for timestamp, _, row in values:
        if not include_auction and _is_auction_time(timestamp.time()):
            continue
        label_time = _transaction_minute_label(timestamp.time(), include_auction)
        if label_time is None:
            if outside_session == "drop":
                continue
            raise ValueError(f"transaction is outside the supported A-share session: {timestamp}")
        label = datetime.combine(timestamp.date(), label_time)
        groups.setdefault(label, []).append(row)

    rows: list[dict[str, object]] = []
    previous_by_day: dict[Date, float] = {}
    for label, group in groups.items():
        prices = [_number(row.get("price"), "transaction price") for row in group]
        volumes = [
            _non_negative_number(_first(row, "volume", "vol"), "transaction volume")
            for row in group
        ]
        amounts = [
            _non_negative_number(row["amount"], "transaction amount")
            if not _is_missing(row.get("amount"))
            else price * volume * int(lot_size)
            for row, price, volume in zip(group, prices, volumes, strict=True)
        ]
        order_values = [row.get("num") for row in group if not _is_missing(row.get("num"))]
        bar: dict[str, object] = {
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": sum(volumes),
            "vol": sum(volumes),
            "amount": sum(amounts),
            "datetime": label.strftime("%Y-%m-%d %H:%M"),
            "previous_close": previous_by_day.get(label.date()),
            "trade_count": len(group),
            "order_count": sum(int(value) for value in order_values) if order_values else None,
            "is_call_auction": label.time() == Time(9, 30),
        }
        rows.append(bar)
        previous_by_day[label.date()] = prices[-1]

    _verify_totals(
        input_volume=sum(
            _non_negative_number(_first(row, "volume", "vol"), "transaction volume")
            for group in groups.values()
            for row in group
        ),
        input_amount=sum(
            _transaction_amount(row, int(lot_size)) for group in groups.values() for row in group
        ),
        output=rows,
    )
    return _bar_frame(rows)


def aggregate_bars(
    bars: Records,
    frequency: int | str,
    *,
    preserve_auction: bool = True,
) -> pd.DataFrame:
    """Aggregate OHLCV bars without crossing dates or the A-share lunch break."""

    unit, size = _normalize_frequency(frequency)
    records = _records(bars)
    if not records:
        return _bar_frame([])

    values: list[tuple[datetime, int, Mapping[str, object]]] = []
    for position, row in enumerate(records):
        values.append((_bar_datetime(row), position, row))
    values.sort(key=lambda item: (item[0], item[1]))

    groups: dict[object, list[tuple[datetime, Mapping[str, object]]]] = {}
    labels: dict[object, datetime] = {}
    for timestamp, _, row in values:
        key, label = _bar_group(timestamp, unit, size, preserve_auction)
        groups.setdefault(key, []).append((timestamp, row))
        labels[key] = label if unit == "minute" else timestamp

    output: list[dict[str, object]] = []
    prior_close: float | None = None
    for key, group in groups.items():
        prices = [
            (
                _number(row.get("open"), "bar open"),
                _number(row.get("high"), "bar high"),
                _number(row.get("low"), "bar low"),
                _number(row.get("close"), "bar close"),
            )
            for _, row in group
        ]
        volumes = [
            _non_negative_number(_first(row, "volume", "vol"), "bar volume")
            for _, row in group
        ]
        amounts = [
            _non_negative_number(row.get("amount"), "bar amount") for _, row in group
        ]
        first_previous = group[0][1].get("previous_close")
        previous_close = (
            _number(first_previous, "bar previous_close")
            if not _is_missing(first_previous)
            else prior_close
        )
        label = labels[key]
        row_out: dict[str, object] = {
            "open": prices[0][0],
            "high": max(item[1] for item in prices),
            "low": min(item[2] for item in prices),
            "close": prices[-1][3],
            "volume": sum(volumes),
            "vol": sum(volumes),
            "amount": sum(amounts),
            "datetime": label.strftime("%Y-%m-%d %H:%M"),
            "previous_close": previous_close,
        }
        trade_counts = [
            row.get("trade_count", row.get("order_count"))
            for _, row in group
            if not _is_missing(row.get("trade_count", row.get("order_count")))
        ]
        if trade_counts:
            row_out["trade_count"] = sum(int(value) for value in trade_counts)
        if unit == "minute":
            row_out["is_call_auction"] = label.time() == Time(9, 30)
        output.append(row_out)
        prior_close = prices[-1][3]

    _verify_totals(
        input_volume=sum(
            _non_negative_number(_first(row, "volume", "vol"), "bar volume")
            for _, _, row in values
        ),
        input_amount=sum(
            _non_negative_number(row.get("amount"), "bar amount") for _, _, row in values
        ),
        output=output,
    )
    return _bar_frame(output)


def _records(values: Records) -> list[dict[str, object]]:
    if isinstance(values, pd.DataFrame):
        frame = values.copy(deep=True)
        if "datetime" not in frame.columns and isinstance(frame.index, pd.DatetimeIndex):
            frame["datetime"] = frame.index
        return [dict(row) for row in frame.to_dict("records")]
    rows: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError("analytics input rows must be mappings")
        rows.append(dict(value))
    return rows


def _first(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if not _is_missing(row.get(key)):
            return row[key]
    raise ValueError(f"row is missing all required fields: {', '.join(keys)}")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _non_negative_number(value: object, name: str) -> float:
    result = _number(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(result)
    except ValueError:
        return False


def _trade_side(row: Mapping[str, object]) -> Side:
    name = str(row.get("side_name", "")).strip().lower()
    if name in {"buy", "b", "买", "买入"}:
        return "buy"
    if name in {"sell", "s", "卖", "卖出"}:
        return "sell"
    if name in {"neutral", "中性"}:
        return "neutral"
    if not _is_missing(row.get("buyorsell")):
        value = int(row["buyorsell"])
        return "buy" if value == 0 else "sell" if value == 1 else "neutral"
    if not _is_missing(row.get("direction")):
        value = int(row["direction"])
        return "buy" if value > 0 else "sell" if value < 0 else "neutral"
    return "neutral"


def _normalize_date(value: str | int | Date) -> Date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    raw = str(value).strip().replace("-", "")
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid transaction date: {value!r}") from exc


def _trade_datetime(row: Mapping[str, object], date: Date | None) -> datetime:
    raw_datetime = row.get("datetime")
    if raw_datetime is not None:
        try:
            return pd.Timestamp(raw_datetime).to_pydatetime()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid transaction datetime: {raw_datetime!r}") from exc
    raw_time = row.get("time")
    if raw_time is None or date is None:
        raise ValueError("transaction rows without datetime require the date argument")
    try:
        parsed_time = Time.fromisoformat(str(raw_time))
    except ValueError as exc:
        raise ValueError(f"invalid transaction time: {raw_time!r}") from exc
    return datetime.combine(date, parsed_time)


def _transaction_minute_label(value: Time, include_auction: bool) -> Time | None:
    minute = value.hour * 60 + value.minute
    if 9 * 60 + 15 <= minute < 9 * 60 + 30:
        return Time(9, 30) if include_auction else None
    if 9 * 60 + 30 <= minute <= 11 * 60 + 30:
        label = min(minute + 1, 11 * 60 + 30)
        return Time(label // 60, label % 60)
    if 13 * 60 <= minute <= 15 * 60:
        label = min(minute + 1, 15 * 60)
        return Time(label // 60, label % 60)
    return None


def _is_auction_time(value: Time) -> bool:
    minute = value.hour * 60 + value.minute
    return 9 * 60 + 15 <= minute < 9 * 60 + 30


def _transaction_amount(row: Mapping[str, object], lot_size: int) -> float:
    if not _is_missing(row.get("amount")):
        return _non_negative_number(row["amount"], "transaction amount")
    return (
        _number(row.get("price"), "transaction price")
        * _non_negative_number(_first(row, "volume", "vol"), "transaction volume")
        * lot_size
    )


def _bar_datetime(row: Mapping[str, object]) -> datetime:
    value = row.get("datetime")
    if value is None:
        raise ValueError("bar rows require a datetime field")
    try:
        return pd.Timestamp(value).to_pydatetime()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid bar datetime: {value!r}") from exc


def _normalize_frequency(value: int | str) -> tuple[str, int]:
    if isinstance(value, bool):
        raise ValueError("frequency must be a positive minute count or calendar frequency")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("minute frequency must be positive")
        return "minute", value
    normalized = str(value).strip().lower()
    minute_aliases = {
        "1m": 1,
        "1min": 1,
        "5m": 5,
        "5min": 5,
        "15m": 15,
        "15min": 15,
        "30m": 30,
        "30min": 30,
        "60m": 60,
        "60min": 60,
        "1h": 60,
    }
    if normalized in minute_aliases:
        return "minute", minute_aliases[normalized]
    aliases = {
        "day": "day",
        "daily": "day",
        "week": "week",
        "weekly": "week",
        "month": "month",
        "monthly": "month",
        "quarter": "quarter",
        "quarterly": "quarter",
        "year": "year",
        "yearly": "year",
    }
    try:
        return aliases[normalized], 1
    except KeyError as exc:
        raise ValueError(f"unsupported aggregation frequency: {value!r}") from exc


def _bar_group(
    timestamp: datetime,
    unit: str,
    size: int,
    preserve_auction: bool,
) -> tuple[object, datetime]:
    if unit == "minute":
        label = _minute_bar_label(timestamp, size, preserve_auction)
        return label, label
    day = timestamp.date()
    if unit == "day":
        return day, timestamp
    if unit == "week":
        iso = day.isocalendar()
        return (iso.year, iso.week), timestamp
    if unit == "month":
        return (day.year, day.month), timestamp
    if unit == "quarter":
        return (day.year, (day.month - 1) // 3 + 1), timestamp
    return day.year, timestamp


def _minute_bar_label(timestamp: datetime, size: int, preserve_auction: bool) -> datetime:
    minute = timestamp.hour * 60 + timestamp.minute
    auction = 9 * 60 + 30
    if minute == auction and preserve_auction:
        return timestamp.replace(second=0, microsecond=0)
    sessions = ((9 * 60 + 31, 11 * 60 + 30), (13 * 60 + 1, 15 * 60))
    for start, end in sessions:
        effective = start if minute == auction and not preserve_auction else minute
        if start <= effective <= end:
            offset = effective - start
            label = min(start + (offset // size + 1) * size - 1, end)
            return timestamp.replace(hour=label // 60, minute=label % 60, second=0, microsecond=0)
    raise ValueError(f"bar is outside the supported A-share session: {timestamp}")


def _verify_totals(
    *,
    input_volume: float,
    input_amount: float,
    output: Iterable[Mapping[str, object]],
) -> None:
    rows = list(output)
    output_volume = sum(_number(row.get("volume"), "bar volume") for row in rows)
    output_amount = sum(_number(row.get("amount"), "bar amount") for row in rows)
    if not math.isclose(input_volume, output_volume, rel_tol=1e-12, abs_tol=1e-9):
        raise RuntimeError("bar aggregation did not conserve volume")
    if not math.isclose(input_amount, output_amount, rel_tol=1e-12, abs_tol=0.01):
        raise RuntimeError("bar aggregation did not conserve amount")


def _bar_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        frame = pd.DataFrame(columns=BAR_COLUMNS)
        frame.index = pd.DatetimeIndex([], name="datetime")
        return frame
    frame = pd.DataFrame.from_records(rows)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame["datetime"]), name="datetime")
    return frame


__all__ = ["aggregate_bars", "summarize_trade_sides", "trades_to_minute_bars"]
