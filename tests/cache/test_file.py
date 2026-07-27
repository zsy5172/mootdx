from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from mootdx.cache import file_cache


def test_file_cache_reuses_fresh_dataframe(tmp_path) -> None:
    cache_file = tmp_path / 'sample.pkl'
    calls = 0

    @file_cache(cache_file, refresh_time=100)
    def sample() -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return pd.DataFrame({'value': [calls]})

    first = sample()
    second = sample()

    pd.testing.assert_frame_equal(first, second)
    assert calls == 1
    assert cache_file.is_file()


def test_file_cache_refreshes_expired_dataframe(tmp_path, monkeypatch) -> None:
    cache_file = tmp_path / 'sample.pkl'
    calls = 0

    @file_cache(cache_file, refresh_time=100)
    def sample() -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return pd.DataFrame({'value': [calls]})

    first = sample()
    monkeypatch.setattr('mootdx.cache.file.time.time', lambda: cache_file.stat().st_mtime + 101)
    second = sample()

    assert first.iloc[0, 0] == 1
    assert second.iloc[0, 0] == 2
    assert calls == 2


def test_file_cache_recovers_from_corrupt_pickle(tmp_path) -> None:
    cache_file = tmp_path / 'sample.pkl'
    cache_file.write_bytes(b'not a pickle')

    @file_cache(cache_file)
    def sample() -> pd.DataFrame:
        return pd.DataFrame({'value': [42]})

    result = sample()

    assert result.iloc[0, 0] == 42
    pd.testing.assert_frame_equal(pd.read_pickle(cache_file), result)


def test_file_cache_serializes_threads_for_the_same_path(tmp_path) -> None:
    cache_file = tmp_path / 'sample.pkl'
    calls = 0

    @file_cache(cache_file)
    def sample() -> pd.DataFrame:
        nonlocal calls
        calls += 1
        return pd.DataFrame({'value': [calls]})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: sample(), range(16)))

    assert calls == 1
    assert all(result.iloc[0, 0] == 1 for result in results)
    assert list(tmp_path.glob('*.tmp')) == []


def test_file_cache_rejects_non_dataframe_results(tmp_path) -> None:
    @file_cache(tmp_path / 'sample.pkl')
    def sample():
        return []

    with pytest.raises(TypeError, match='must return pandas.DataFrame'):
        sample()
