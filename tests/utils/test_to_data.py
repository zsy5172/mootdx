from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt

from mootdx.utils import to_data


def test_to_data_accepts_numpy_and_series_without_truth_value_checks() -> None:
    array = to_data(np.array([[1, 2], [3, 4]]))
    series = to_data(pd.Series([1, 2], name='value'))

    pdt.assert_frame_equal(array, pd.DataFrame([[1, 2], [3, 4]]))
    pdt.assert_frame_equal(series, pd.DataFrame({'value': [1, 2]}))


def test_to_data_preserves_empty_and_unsupported_input_semantics() -> None:
    for value in (None, {}, [], (), 'not tabular', 123):
        assert to_data(value).empty


def test_to_data_does_not_mutate_source_dataframe() -> None:
    source = pd.DataFrame(
        {
            'datetime': ['2026-07-27 09:30:00'],
            'vol': [100],
        }
    )

    result = to_data(source)

    assert 'volume' not in source.columns
    assert isinstance(source.index, pd.RangeIndex)
    assert result['volume'].tolist() == [100]
    assert result.index.equals(pd.DatetimeIndex(['2026-07-27 09:30:00'], name='datetime'))


def test_to_data_passes_explicit_xdxr_to_adjustment(monkeypatch) -> None:
    actions = pd.DataFrame([{'category': 1}])
    calls: list[tuple[object, ...]] = []

    def fake_adjust(frame, *, symbol, adjust, xdxr):
        calls.append((symbol, adjust, xdxr))
        return frame

    monkeypatch.setattr('mootdx.utils.adjust.to_adjust', fake_adjust)

    result = to_data(
        [{'close': 10.0}],
        symbol='600036',
        adjust='QFQ',
        xdxr=actions,
    )

    assert not result.empty
    assert len(calls) == 1
    assert calls[0][:2] == ('600036', 'qfq')
    assert calls[0][2] is actions


def test_to_data_coerces_invalid_dates_without_raising() -> None:
    result = to_data([{'date': 'invalid', 'close': 1.0}])

    assert result.index.isna().all()
