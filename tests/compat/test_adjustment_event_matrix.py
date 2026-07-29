from __future__ import annotations

from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest

from compat.common import load_json
from mootdx_next import PandasClient
from mootdx_next.adjustments import AdjustmentService
from mootdx_next.errors import AdjustmentError

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


class TdxDesktopBaselineClient:
    closed = False

    def __init__(self) -> None:
        self.daily = [
            {
                "datetime": "2002-04-09 15:00:00",
                "open": 10.51,
                "high": 10.88,
                "low": 10.51,
                "close": 10.66,
            },
            {
                "datetime": "2006-01-11 15:00:00",
                "open": 7.79,
                "high": 7.88,
                "low": 7.60,
                "close": 7.68,
            },
            {
                "datetime": "2006-02-27 15:00:00",
                "open": 6.80,
                "high": 6.88,
                "low": 6.61,
                "close": 6.66,
            },
            {
                "datetime": "2006-02-28 15:00:00",
                "open": 6.65,
                "high": 6.75,
                "low": 6.43,
                "close": 6.69,
            },
            {
                "datetime": "2026-07-29 15:00:00",
                "open": 39.98,
                "high": 40.07,
                "low": 39.49,
                "close": 39.66,
            },
        ]
        self.events = _records("xdxr", "sh_600036")

    def bars(self, symbol: str, frequency=9, start=0, offset=800):
        assert symbol.lower().replace("sh", "") == "600036"
        assert frequency == 9
        return self.daily if start == 0 else []

    def xdxr(self, symbol: str):
        assert symbol.lower().replace("sh", "") == "600036"
        return self.events


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


def test_real_600036_warrant_event_is_pinned_as_an_unvalued_input() -> None:
    events = [
        row
        for row in _records("xdxr", "sh_600036")
        if row["category"] in {1, 14} and _event_date(row) == "2006-02-27"
    ]

    assert [row["category"] for row in events] == [1, 14]
    assert events[0]["songzhuangu"] == pytest.approx(2.5962998867)
    assert events[1]["fenshu"] == pytest.approx(6)
    assert events[1]["xingquanjia"] == pytest.approx(5.65)


@pytest.mark.parametrize(
    ("adjust", "expected_rows"),
    [
        (
            "tdx_qfq",
            [
                [-12.13, -12.09, -12.22, -12.18],
                [-11.77, -11.73, -11.89, -11.86],
                [-11.86, -11.80, -11.99, -11.84],
            ],
        ),
        (
            "tdx_hfq",
            [
                [14.37, 14.53, 14.02, 14.17],
                [15.76, 15.94, 15.33, 15.44],
                [15.42, 15.65, 14.92, 15.51],
            ],
        ),
    ],
)
def test_real_600036_tdx_adjustment_matches_desktop_capture(
    adjust: str,
    expected_rows: list[list[float]],
) -> None:
    service = AdjustmentService(TdxDesktopBaselineClient())
    dates = pd.to_datetime(
        [
            "2006-01-11 15:00:00",
            "2006-02-27 15:00:00",
            "2006-02-28 15:00:00",
        ]
    )
    expected = pd.DataFrame(expected_rows, index=dates, columns=PRICE_COLUMNS)

    actual = service.adjusted_daily("600036", adjust).loc[dates, PRICE_COLUMNS]

    pdt.assert_frame_equal(actual, expected, check_freq=False)


def test_real_600036_proportional_adjustment_keeps_warrant_guard() -> None:
    service = AdjustmentService(TdxDesktopBaselineClient())

    with pytest.raises(AdjustmentError, match="category 14"):
        service.adjusted_daily("600036", "qfq")


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
        # The pinned bar corpus begins after the earlier split events, so this
        # snapshot uses its first bar as the baseline and only undoes the cash
        # event contained in the window.
        assert adjusted.at[event_date, "close"] == pytest.approx(8.151 + 0.149)


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
