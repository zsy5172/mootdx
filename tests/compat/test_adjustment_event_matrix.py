from __future__ import annotations

from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest

from compat.common import load_json
from mootdx_next import PandasClient
from mootdx_next.adjustments import AdjustmentService

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "compat" / "corpus"
PRICE_COLUMNS = ["open", "high", "low", "close"]


def _records(api: str, case_id: str) -> list[dict[str, object]]:
    artifact = load_json(CORPUS / api / case_id / "expected.json")
    assert artifact["status"] == "ok"
    return artifact["result"]["records"]


class RecordedAdjustmentClient:
    closed = False

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.daily = _records("bars", f"daily_sh_{symbol}_last60_adjustment_window")
        self.events = _records("xdxr", f"sh_{symbol}")

    def bars(self, symbol: str, frequency=9, start=0, offset=800):
        assert symbol.lower().replace("sh", "") == self.symbol
        assert frequency == 9
        return self.daily if start == 0 else []

    def xdxr(self, symbol: str):
        assert symbol.lower().replace("sh", "") == self.symbol
        return self.events

    def close(self) -> None:
        self.closed = True


def _event_date(row: dict[str, object]) -> str:
    return f"{int(row['year']):04d}-{int(row['month']):02d}-{int(row['day']):02d}"


def test_real_600036_cash_events_are_pinned_in_legacy_corpus() -> None:
    events = [
        row
        for row in _records("xdxr", "sh_600036")
        if row["category"] == 1 and int(row["year"]) == 2026
    ]

    assert [_event_date(row) for row in events] == ["2026-01-16", "2026-07-10"]
    assert [row["fenhong"] for row in events] == pytest.approx([10.13, 10.03])


def test_real_510500_etf_action_sequence_is_pinned_in_legacy_corpus() -> None:
    events = _records("xdxr", "sh_510500")

    assert [_event_date(row) for row in events] == [
        "2015-04-15",
        "2022-08-29",
        "2024-05-17",
        "2025-01-16",
        "2026-01-16",
        "2026-07-15",
    ]
    assert [row["category"] for row in events] == [11, 11, 1, 1, 1, 1]
    assert [events[0]["suogu"], events[1]["suogu"]] == pytest.approx([0.2803248167, 1.1453900337])
    assert [row["fenhong"] for row in events[2:]] == pytest.approx([0.87, 0.91, 0.62, 1.49])


def test_real_600036_qfq_uses_the_latest_cash_event_ratio() -> None:
    service = AdjustmentService(RecordedAdjustmentClient("600036"))
    adjusted = service.adjusted_daily("600036", "qfq")

    previous_date = pd.Timestamp("2026-07-09 15:00:00")
    event_date = pd.Timestamp("2026-07-10 15:00:00")
    previous_close = 37.55
    dividend_per_share = 10.029999732971191 / 10
    expected_ratio = (previous_close - dividend_per_share) / previous_close

    assert adjusted.at[previous_date, "factor"] == pytest.approx(expected_ratio)
    assert adjusted.at[previous_date, "close"] == pytest.approx(previous_close * expected_ratio)
    assert adjusted.at[event_date, "factor"] == pytest.approx(1)
    assert adjusted.at[event_date, "close"] == pytest.approx(36.88)


@pytest.mark.parametrize("adjust", ["qfq", "hfq"])
def test_real_510500_etf_cash_event_uses_affine_adjustment(adjust: str) -> None:
    service = AdjustmentService(RecordedAdjustmentClient("510500"))
    adjusted = service.adjusted_daily("510500", adjust)

    previous_date = pd.Timestamp("2026-07-14 15:00:00")
    event_date = pd.Timestamp("2026-07-15 15:00:00")
    if adjust == "qfq":
        assert adjusted.at[previous_date, "close"] == pytest.approx(8.413 - 0.149)
        assert adjusted.at[event_date, "close"] == pytest.approx(8.151)
    else:
        assert adjusted.loc[[previous_date, event_date], PRICE_COLUMNS].notna().all().all()
        assert (adjusted.loc[[previous_date, event_date], PRICE_COLUMNS] > 0).all().all()


@pytest.mark.parametrize(
    ("symbol", "adjust"),
    [("600036", "qfq"), ("510500", "qfq"), ("510500", "hfq")],
)
@pytest.mark.parametrize(
    ("frequency", "period"),
    [(5, "W-FRI"), (6, "M"), (10, "Q-DEC"), (11, "Y-DEC")],
)
def test_real_period_adjustment_equals_adjusted_daily_then_aggregated(
    symbol: str,
    adjust: str,
    frequency: int,
    period: str,
) -> None:
    service = AdjustmentService(RecordedAdjustmentClient(symbol))
    daily = service.adjusted_daily(symbol, adjust)
    actual = service.adjusted_bars(symbol, adjust, frequency=frequency, start=0, offset=800)
    groups = daily.groupby(daily.index.to_period(period))
    expected = pd.DataFrame(
        {
            "open": groups["open"].first(),
            "high": groups["high"].max(),
            "low": groups["low"].min(),
            "close": groups["close"].last(),
        }
    )
    expected.index = pd.DatetimeIndex(groups.apply(lambda group: group.index[-1]).to_numpy())
    expected.index.name = "datetime"

    pdt.assert_frame_equal(actual[PRICE_COLUMNS], expected[PRICE_COLUMNS], check_freq=False)


@pytest.mark.parametrize(
    ("api", "kwargs"),
    [
        (
            "get_k_data",
            {"code": "510500", "start_date": "2026-07-08", "end_date": "2026-07-17"},
        ),
        (
            "k",
            {"symbol": "510500", "begin": "2026-07-08", "end": "2026-07-17"},
        ),
        (
            "ohlc",
            {"symbol": "510500", "begin": "2026-07-08", "end": "2026-07-17"},
        ),
    ],
)
@pytest.mark.parametrize("adjust", ["qfq", "hfq"])
def test_real_etf_history_wrappers_return_adjusted_data(
    api: str,
    kwargs: dict[str, str],
    adjust: str,
) -> None:
    client = PandasClient(raw_client=RecordedAdjustmentClient("510500"))

    result = getattr(client, api)(**kwargs, adjust=adjust)

    assert not result.empty
    assert result.index.min() == pd.Timestamp("2026-07-08")
    assert result.index.max() == pd.Timestamp("2026-07-17")
    assert "factor" in result.columns
    if api == "get_k_data":
        assert "volume" not in result.columns
    else:
        assert "volume" in result.columns
