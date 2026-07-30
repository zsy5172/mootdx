from __future__ import annotations

import math
from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime

from mootdx_next.errors import ProtocolDecodeError

MinuteBar = Mapping[str, object]
Transaction = Mapping[str, object]
TransactionsByDate = Mapping[str, Sequence[Transaction]]


def rebuild_minute_bars_241(
    bars: Sequence[MinuteBar],
    transactions_by_date: TransactionsByDate,
) -> list[dict[str, object]]:
    """Split call-auction turnover out of the wire-level 09:31 minute bar.

    TDX standard minute bars contain 240 records.  The opening call auction is
    included in the record labelled 09:31.  A desktop-style 241-record series
    represents the auction as 09:30 and deducts its volume and amount from
    09:31.  All inputs are copied; neither the bars nor transactions are
    mutated.
    """

    rows = [dict(row) for row in bars]
    rebuilt: list[dict[str, object]] = []

    for row in rows:
        row["is_call_auction"] = False
        row["auction_adjusted"] = False
        day, time_value = _bar_day_and_time(row)
        if time_value != "09:31":
            rebuilt.append(row)
            continue

        transactions = list(transactions_by_date.get(day, ()))
        auction = [item for item in transactions if _is_auction_transaction(item)]
        if not auction:
            rebuilt.append(row)
            continue

        synthetic = _auction_bar(day, row, auction)
        adjusted = _deduct_auction(row, auction, transactions)
        _verify_conservation(row, synthetic, adjusted)
        rebuilt.extend((synthetic, adjusted))

    return rebuilt


def _bar_day_and_time(row: MinuteBar) -> tuple[str, str]:
    value = str(row.get("datetime", ""))
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ProtocolDecodeError(f"minute bar has an invalid datetime: {value!r}") from exc
    return parsed.strftime("%Y%m%d"), parsed.strftime("%H:%M")


def _is_auction_transaction(row: Transaction) -> bool:
    time_value = str(row.get("time", ""))
    return bool(time_value) and time_value < "09:30" and _number(row.get("price")) > 0


def _auction_bar(
    day: str,
    source: MinuteBar,
    transactions: Sequence[Transaction],
) -> dict[str, object]:
    prices = [_number(item.get("price")) for item in transactions]
    volume = sum(_transaction_volume(item) for item in transactions)
    amount = sum(_transaction_amount(item) for item in transactions)
    order_count = _transaction_order_count(transactions)
    parsed = datetime.strptime(day, "%Y%m%d")

    return {
        "open": prices[0],
        "close": prices[-1],
        "high": max(prices),
        "low": min(prices),
        "vol": volume,
        "volume": volume,
        "amount": amount,
        "year": parsed.year,
        "month": parsed.month,
        "day": parsed.day,
        "hour": 9,
        "minute": 30,
        "datetime": parsed.strftime("%Y-%m-%d 09:30"),
        "previous_close": source.get("previous_close"),
        "order_count": order_count,
        "is_call_auction": True,
        "auction_adjusted": False,
    }


def _deduct_auction(
    source: MinuteBar,
    auction: Sequence[Transaction],
    all_transactions: Sequence[Transaction],
) -> dict[str, object]:
    row = dict(source)
    original_volume = _bar_volume(source)
    original_amount = _number(source.get("amount"))
    auction_volume = sum(_transaction_volume(item) for item in auction)
    auction_amount = sum(_transaction_amount(item) for item in auction)
    remaining_volume = original_volume - auction_volume
    remaining_amount = original_amount - auction_amount
    tolerance = max(0.01, abs(original_amount) * 1e-12)

    if remaining_volume < 0 or remaining_amount < -tolerance:
        raise ProtocolDecodeError(
            "call-auction turnover exceeds the source 09:31 minute bar; "
            "the bar and transaction snapshots are inconsistent"
        )
    if remaining_amount < 0:
        remaining_amount = 0.0

    regular = [item for item in all_transactions if str(item.get("time", "")) == "09:30"]
    if regular:
        row["open"] = _number(regular[0].get("price"))
        row["order_count"] = _transaction_order_count(regular)
    else:
        row["order_count"] = None
    row["vol"] = remaining_volume
    row["volume"] = remaining_volume
    row["amount"] = remaining_amount
    row["previous_close"] = _number(auction[-1].get("price"))
    row["is_call_auction"] = False
    row["auction_adjusted"] = True
    return row


def _verify_conservation(
    source: MinuteBar,
    auction: MinuteBar,
    adjusted: MinuteBar,
) -> None:
    original_volume = _bar_volume(source)
    rebuilt_volume = _bar_volume(auction) + _bar_volume(adjusted)
    original_amount = _number(source.get("amount"))
    rebuilt_amount = _number(auction.get("amount")) + _number(adjusted.get("amount"))
    if not math.isclose(original_volume, rebuilt_volume, rel_tol=0.0, abs_tol=1e-9):
        raise ProtocolDecodeError("241-minute reconstruction did not conserve volume")
    if not math.isclose(original_amount, rebuilt_amount, rel_tol=0.0, abs_tol=0.01):
        raise ProtocolDecodeError("241-minute reconstruction did not conserve amount")


def _bar_volume(row: MinuteBar) -> float:
    value = row.get("vol", row.get("volume"))
    return _number(value)


def _transaction_volume(row: Transaction) -> float:
    return _number(row.get("vol", row.get("volume")))


def _transaction_amount(row: Transaction) -> float:
    value = row.get("amount")
    if value is not None:
        return _number(value)
    return _number(row.get("price")) * _transaction_volume(row) * 100.0


def _transaction_order_count(rows: Sequence[Transaction]) -> int | None:
    values = [row.get("num") for row in rows]
    if not any(value is not None for value in values):
        return None
    return sum(int(value or 0) for value in values)


def _number(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolDecodeError(f"expected a numeric minute-bar value, got {value!r}") from exc
    if not math.isfinite(result):
        raise ProtocolDecodeError(f"minute-bar value is not finite: {value!r}")
    return result


__all__ = ["rebuild_minute_bars_241"]
