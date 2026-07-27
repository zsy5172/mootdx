from __future__ import annotations

import pandas as pd

from mootdx.utils.pandas_cache import pd_cache
from mootdx.utils.pandas_cache import pd_cached_delete


def test_pd_cache_keeps_distinct_argument_entries(tmp_path) -> None:
    calls = 0

    @pd_cache(tmp_path)
    def sample(value: int) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return pd.DataFrame({'value': [value], 'call': [calls]})

    first = sample(1)
    repeated = sample(1)
    second = sample(2)

    pd.testing.assert_frame_equal(first, repeated)
    assert calls == 2
    assert second.iloc[0]['value'] == 2
    assert len(list(tmp_path.glob('sample_*.pkl'))) == 2


def test_pd_cache_recovers_from_corrupt_entry(tmp_path) -> None:
    calls = 0

    @pd_cache(tmp_path)
    def sample() -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return pd.DataFrame({'call': [calls]})

    sample()
    cache_file = next(tmp_path.glob('sample_*.pkl'))
    cache_file.write_bytes(b'broken')

    result = sample()

    assert calls == 2
    assert result.iloc[0, 0] == 2


def test_pd_cached_delete_preserves_legacy_return_shape(tmp_path) -> None:
    (tmp_path / 'one.pkl').write_bytes(b'one')
    (tmp_path / 'two.pkl').write_bytes(b'two')

    result = pd_cached_delete(tmp_path)

    assert result.startswith('removed ')
    assert list(tmp_path.glob('*.pkl')) == []
    assert pd_cached_delete(tmp_path) == 'No cached DataFrames'
