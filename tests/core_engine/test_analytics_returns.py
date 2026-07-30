from __future__ import annotations

import pandas as pd
import pytest

from mootdx_next.analytics import forward_returns


def test_forward_returns_use_trading_rows_instead_of_calendar_days() -> None:
    index = pd.to_datetime(["2026-07-17", "2026-07-20", "2026-07-21", "2026-07-24"])
    frame = pd.DataFrame({"close": [10.0, 11.0, 12.0, 15.0]}, index=index)
    original = frame.copy(deep=True)

    result = forward_returns(frame, horizons=(1, 2))

    assert result.loc["2026-07-17", "return_1"] == pytest.approx(0.1)
    assert result.loc["2026-07-17", "return_2"] == pytest.approx(0.2)
    assert result.loc["2026-07-21", "return_1"] == pytest.approx(0.25)
    assert pd.isna(result.loc["2026-07-24", "return_1"])
    assert pd.isna(result.loc["2026-07-21", "return_2"])
    pd.testing.assert_frame_equal(frame, original)


def test_forward_returns_can_report_percent_values() -> None:
    values = pd.Series([10.0, 11.0], name="close")

    result = forward_returns(values, horizons=(1,), percent=True)

    assert result.iloc[0]["return_1"] == pytest.approx(10.0)


def test_forward_returns_never_cross_group_boundaries() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["A", "B", "A", "B"],
            "close": [10.0, 100.0, 12.0, 90.0],
        }
    )

    result = forward_returns(frame, horizons=(1,), group_by="symbol")

    assert result["return_1"].tolist()[:2] == pytest.approx([0.2, -0.1])
    assert result["return_1"].iloc[2:].isna().all()


def test_forward_returns_preserve_nan_and_empty_shape() -> None:
    frame = pd.DataFrame({"close": [10.0, float("nan"), 12.0]})

    result = forward_returns(frame, horizons=(1, 2))
    empty = forward_returns(pd.DataFrame({"close": []}), horizons=(1, 5))

    assert pd.isna(result.iloc[0]["return_1"])
    assert result.iloc[0]["return_2"] == pytest.approx(0.2)
    assert empty.empty
    assert list(empty.columns) == ["return_1", "return_5"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: forward_returns([1, 2]),
        lambda: forward_returns(pd.DataFrame({"open": [1]})),
        lambda: forward_returns(pd.Series([1]), horizons=()),
        lambda: forward_returns(pd.Series([1]), horizons=(0,)),
        lambda: forward_returns(pd.Series([1]), horizons=(1, 1)),
        lambda: forward_returns(pd.Series([0, 1]), horizons=(1,)),
        lambda: forward_returns(pd.Series([1, float("inf")]), horizons=(1,)),
        lambda: forward_returns(pd.Series([1]), horizons=(1,), group_by="symbol"),
    ],
)
def test_forward_returns_validation(call) -> None:
    with pytest.raises((TypeError, ValueError)):
        call()
