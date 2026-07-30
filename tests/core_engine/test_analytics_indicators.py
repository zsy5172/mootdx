from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from mootdx_next.analytics import atr
from mootdx_next.analytics import boll
from mootdx_next.analytics import ema
from mootdx_next.analytics import hhv
from mootdx_next.analytics import llv
from mootdx_next.analytics import ma
from mootdx_next.analytics import macd
from mootdx_next.analytics import ref
from mootdx_next.analytics import rsi
from mootdx_next.analytics import vwap


def test_ref_hhv_llv_and_ma_return_complete_aligned_series() -> None:
    index = pd.date_range("2026-07-01", periods=5)
    frame = pd.DataFrame(
        {
            "close": [1, 2, 3, 4, 5],
            "high": [2, 4, 3, 6, 5],
            "low": [1, 0, 2, 3, 4],
        },
        index=index,
    )

    referenced = ref(frame, 2)
    assert referenced.iloc[:2].isna().all()
    assert referenced.iloc[2:].tolist() == [1.0, 2.0, 3.0]
    assert hhv(frame, 3).iloc[2:].tolist() == [4.0, 6.0, 6.0]
    assert llv(frame, 3).iloc[2:].tolist() == [0.0, 0.0, 2.0]
    assert ma(frame, 3).iloc[2:].tolist() == [2.0, 3.0, 4.0]
    assert ma(frame, 3).index.equals(index)


def test_ema_matches_float_pandas_recursion_without_integer_truncation() -> None:
    values = pd.Series([1.0, 2.0, 4.0, 8.0])

    result = ema(values, 3)
    expected = values.ewm(span=3, adjust=False, min_periods=1).mean()

    pd.testing.assert_series_equal(result, expected.rename("ema_3"))
    assert result.iloc[-1] == pytest.approx(5.375)


def test_macd_returns_full_tdx_double_histogram() -> None:
    values = pd.Series([10.0, 11.0, 10.5, 12.0, 13.0])

    result = macd(values, fast=2, slow=3, signal=2)

    assert list(result.columns) == ["dif", "dea", "histogram"]
    assert result.index.equals(values.index)
    pd.testing.assert_series_equal(
        result["histogram"],
        ((result["dif"] - result["dea"]) * 2).rename("histogram"),
    )
    assert result.iloc[-1]["dif"] != round(float(result.iloc[-1]["dif"]), 2)


def test_rsi_uses_wilder_smoothing_and_keeps_fractional_precision() -> None:
    increasing = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    mixed = pd.Series([10.0, 11.0, 10.0, 12.0, 11.5, 13.0])

    assert rsi(increasing, 3).dropna().tolist() == [100.0, 100.0]
    mixed_result = rsi(mixed, 3).dropna()
    assert (mixed_result >= 0).all() and (mixed_result <= 100).all()
    assert any(value != round(float(value)) for value in mixed_result)


def test_rsi_constant_prices_resolve_zero_denominator_to_zero() -> None:
    result = rsi(pd.Series([10.0] * 6), 3)

    assert result.dropna().tolist() == [0.0, 0.0, 0.0]


def test_boll_uses_population_standard_deviation() -> None:
    values = pd.Series([1.0, 2.0, 3.0])

    result = boll(values, 3, deviations=2)

    assert result.iloc[-1]["middle"] == 2
    expected_std = values.std(ddof=0)
    assert result.iloc[-1]["upper"] == pytest.approx(2 + 2 * expected_std)
    assert result.iloc[-1]["lower"] == pytest.approx(2 - 2 * expected_std)


def test_atr_uses_exact_wilder_seed_and_recursion_without_mutation() -> None:
    frame = pd.DataFrame(
        {
            "high": [10.0, 12.0, 13.0, 15.0],
            "low": [8.0, 9.0, 11.0, 12.0],
            "close": [9.0, 11.0, 12.0, 14.0],
        }
    )
    original = frame.copy(deep=True)

    result = atr(frame, 3)

    assert result.iloc[:2].isna().all()
    assert result.iloc[2] == pytest.approx(7 / 3)
    assert result.iloc[3] == pytest.approx(((7 / 3) * 2 + 3) / 3)
    pd.testing.assert_frame_equal(frame, original)


def test_vwap_has_explicit_lot_units_and_zero_volume_nan() -> None:
    frame = pd.DataFrame(
        {
            "amount": [1000.0, 2200.0, 0.0],
            "volume": [1.0, 2.0, 0.0],
        }
    )

    cumulative = vwap(frame)
    rolling = vwap(frame, window=1)

    assert cumulative.iloc[0] == 10
    assert cumulative.iloc[1] == pytest.approx(3200 / 300)
    assert cumulative.iloc[2] == pytest.approx(3200 / 300)
    assert rolling.iloc[:2].tolist() == [10.0, 11.0]
    assert pd.isna(rolling.iloc[2])


def test_indicators_accept_iterables_and_preserve_nan_warmup() -> None:
    result = ma([1, 2, 3], 2)

    assert pd.isna(result.iloc[0])
    assert result.iloc[1:].tolist() == [1.5, 2.5]


def test_indicator_nan_resets_wilder_seed() -> None:
    frame = pd.DataFrame(
        {
            "high": [2.0, 3.0, 4.0, float("nan"), 5.0, 6.0],
            "low": [1.0, 1.0, 2.0, float("nan"), 3.0, 4.0],
            "close": [1.5, 2.0, 3.0, float("nan"), 4.0, 5.0],
        }
    )

    result = atr(frame, 2)

    assert result.iloc[1] == pytest.approx(1.5)
    assert pd.isna(result.iloc[3])
    assert result.iloc[5] == pytest.approx(2.0)


def test_indicators_do_not_mutate_input_records() -> None:
    frame = pd.DataFrame({"close": [1, 2, 3]})
    original = deepcopy(frame)

    ma(frame, 2)
    ema(frame, 2)
    macd(frame)

    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: ma([1, 2], 0), ValueError),
        (lambda: ref([1, 2], -1), ValueError),
        (lambda: ema([1, 2], 2, min_periods=3), ValueError),
        (lambda: macd([1, 2], fast=3, slow=2), ValueError),
        (lambda: boll([1, 2], 2, deviations=float("nan")), ValueError),
        (lambda: atr(pd.DataFrame({"high": [1]}), 2), ValueError),
        (lambda: vwap(pd.DataFrame({"amount": [1]})), ValueError),
        (lambda: ma(pd.Series([1, float("inf")]), 2), ValueError),
    ],
)
def test_indicator_validation_is_explicit(call, error) -> None:
    with pytest.raises(error):
        call()
