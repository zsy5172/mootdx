from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from mootdx_next.analytics import aggregate_bars
from mootdx_next.analytics import summarize_trade_sides
from mootdx_next.analytics import trades_to_minute_bars


def _bar(
    timestamp: str,
    price: float,
    *,
    volume: float = 1,
    amount: float | None = None,
) -> dict[str, object]:
    return {
        "datetime": timestamp,
        "open": price,
        "high": price + 0.2,
        "low": price - 0.1,
        "close": price + 0.1,
        "volume": volume,
        "vol": volume,
        "amount": price * volume * 100 if amount is None else amount,
    }


def test_summarize_trade_sides_preserves_all_side_and_unit_totals() -> None:
    trades = [
        {"price": 10, "volume": 2, "buyorsell": 0},
        {"price": 11, "vol": 3, "side_name": "sell", "amount": 3300},
        {"price": 12, "volume": 4, "buyorsell": 2},
        {"price": 13, "volume": 5, "direction": 1},
    ]
    original = deepcopy(trades)

    result = summarize_trade_sides(trades)

    assert result == {
        "buy_trade_count": 2,
        "buy_volume": 7.0,
        "buy_amount": 8500.0,
        "sell_trade_count": 1,
        "sell_volume": 3.0,
        "sell_amount": 3300.0,
        "neutral_trade_count": 1,
        "neutral_volume": 4.0,
        "neutral_amount": 4800.0,
        "total_trade_count": 4,
        "total_volume": 14.0,
        "total_amount": 16600.0,
    }
    assert trades == original


def test_summarize_trade_sides_accepts_frames_and_empty_input() -> None:
    frame = pd.DataFrame([{"price": 2.0, "vol": 3, "buyorsell": 0}])

    assert summarize_trade_sides(frame, lot_size=10)["total_amount"] == 60
    assert summarize_trade_sides([])["total_trade_count"] == 0


@pytest.mark.parametrize(
    "trades",
    [
        [{"price": 1, "volume": -1}],
        [{"price": float("nan"), "volume": 1}],
        [object()],
    ],
)
def test_summarize_trade_sides_rejects_invalid_rows(trades) -> None:
    with pytest.raises((TypeError, ValueError)):
        summarize_trade_sides(trades)


def test_trades_to_minute_bars_uses_tdx_interval_end_labels() -> None:
    trades = [
        {"datetime": "2026-07-30 13:00", "price": 10.5, "vol": 5, "buyorsell": 1},
        {"datetime": "2026-07-30 09:25", "price": 10.0, "vol": 2, "buyorsell": 0},
        {"datetime": "2026-07-30 09:30", "price": 10.1, "vol": 3, "buyorsell": 0},
        {"datetime": "2026-07-30 11:30", "price": 10.4, "vol": 4, "buyorsell": 1},
        {"datetime": "2026-07-30 15:00", "price": 10.6, "vol": 6, "buyorsell": 2},
    ]
    original = deepcopy(trades)

    result = trades_to_minute_bars(trades)

    assert list(result["datetime"]) == [
        "2026-07-30 09:30",
        "2026-07-30 09:31",
        "2026-07-30 11:30",
        "2026-07-30 13:01",
        "2026-07-30 15:00",
    ]
    assert list(result["is_call_auction"]) == [True, False, False, False, False]
    assert result["volume"].sum() == 20
    assert result["amount"].sum() == pytest.approx(
        sum(float(row["price"]) * float(row["vol"]) * 100 for row in trades)
    )
    assert trades == original


def test_trades_to_minute_bars_builds_ohlc_and_order_counts_stably() -> None:
    trades = [
        {"datetime": "2026-07-30 09:30:30", "price": 10.2, "volume": 2, "num": 3},
        {"datetime": "2026-07-30 09:30:10", "price": 10.0, "volume": 1, "num": 2},
        {"datetime": "2026-07-30 09:30:40", "price": 9.9, "volume": 4, "num": 1},
    ]

    result = trades_to_minute_bars(trades)
    row = result.iloc[0]

    assert row["open"] == 10.0
    assert row["high"] == 10.2
    assert row["low"] == 9.9
    assert row["close"] == 9.9
    assert row["volume"] == 7
    assert row["order_count"] == 6


def test_trades_to_minute_bars_requires_date_for_time_only_rows() -> None:
    trades = [{"time": "09:30", "price": 10.0, "vol": 1}]

    with pytest.raises(ValueError, match="date argument"):
        trades_to_minute_bars(trades)

    result = trades_to_minute_bars(trades, date="20260730")
    assert result.iloc[0]["datetime"] == "2026-07-30 09:31"


def test_trades_to_minute_bars_controls_outside_session_rows() -> None:
    trades = [
        {"datetime": "2026-07-30 08:00", "price": 10.0, "vol": 1},
        {"datetime": "2026-07-30 12:00", "price": 10.0, "vol": 1},
    ]

    with pytest.raises(ValueError, match="outside"):
        trades_to_minute_bars(trades)
    assert trades_to_minute_bars(trades, outside_session="drop").empty


def test_trades_to_minute_bars_can_exclude_call_auction() -> None:
    trades = [
        {"datetime": "2026-07-30 09:25", "price": 10.0, "vol": 1},
        {"datetime": "2026-07-30 09:30", "price": 10.1, "vol": 2},
    ]

    result = trades_to_minute_bars(trades, include_auction=False)

    assert list(result["datetime"]) == ["2026-07-30 09:31"]
    assert result.iloc[0]["volume"] == 2


def test_aggregate_minute_bars_preserves_auction_and_lunch_boundary() -> None:
    bars = [
        _bar("2026-07-30 13:01", 20.0, volume=7),
        _bar("2026-07-30 09:30", 9.8, volume=2),
        *[
            _bar(f"2026-07-30 09:{minute:02d}", 10.0 + minute / 100, volume=minute)
            for minute in range(31, 37)
        ],
        _bar("2026-07-30 11:30", 12.0, volume=5),
    ]
    original = deepcopy(bars)

    result = aggregate_bars(bars, "5min")

    assert list(result["datetime"]) == [
        "2026-07-30 09:30",
        "2026-07-30 09:35",
        "2026-07-30 09:40",
        "2026-07-30 11:30",
        "2026-07-30 13:05",
    ]
    first_regular = result.iloc[1]
    assert first_regular["open"] == pytest.approx(10.31)
    assert first_regular["close"] == pytest.approx(10.45)
    assert result["volume"].sum() == sum(float(row["volume"]) for row in bars)
    assert result["amount"].sum() == pytest.approx(sum(float(row["amount"]) for row in bars))
    assert bars == original


def test_aggregate_daily_and_calendar_bars_use_last_real_timestamp() -> None:
    bars = [
        _bar("2026-07-30 09:30", 10.0, volume=2),
        _bar("2026-07-30 15:00", 11.0, volume=3),
        _bar("2026-07-31 09:30", 12.0, volume=4),
        _bar("2026-07-31 15:00", 13.0, volume=5),
    ]

    daily = aggregate_bars(bars, "day")
    weekly = aggregate_bars(daily, "week")

    assert list(daily["datetime"]) == ["2026-07-30 15:00", "2026-07-31 15:00"]
    assert daily.iloc[0]["open"] == 10
    assert daily.iloc[0]["close"] == pytest.approx(11.1)
    assert list(weekly["datetime"]) == ["2026-07-31 15:00"]
    assert weekly.iloc[0]["volume"] == 14


@pytest.mark.parametrize("frequency", ["120m", "120min"])
def test_aggregate_bars_accepts_120_minute_aliases(frequency: str) -> None:
    bars = [
        _bar("2026-07-30 09:31", 10.0, volume=2),
        _bar("2026-07-30 11:30", 11.0, volume=3),
        _bar("2026-07-30 13:01", 12.0, volume=4),
        _bar("2026-07-30 15:00", 13.0, volume=5),
    ]

    result = aggregate_bars(bars, frequency)

    assert list(result["datetime"]) == ["2026-07-30 11:30", "2026-07-30 15:00"]
    assert list(result["volume"]) == [5.0, 9.0]


def test_aggregate_bars_returns_typed_empty_frame() -> None:
    result = aggregate_bars([], "month")

    assert result.empty
    assert list(result.columns) == [
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
    assert isinstance(result.index, pd.DatetimeIndex)


@pytest.mark.parametrize("frequency", [0, "2fortnights"])
def test_aggregate_bars_rejects_invalid_frequency(frequency) -> None:
    with pytest.raises(ValueError):
        aggregate_bars([_bar("2026-07-30 09:31", 10.0)], frequency)
