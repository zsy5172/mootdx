from __future__ import annotations

import functools
import hashlib
import inspect
import pickle
import time
from pathlib import Path

import pandas as pd

from mootdx.cache.file import _atomic_pickle
from mootdx.logger import logger

_CACHE_READ_ERRORS = (
    AttributeError,
    EOFError,
    ImportError,
    OSError,
    ValueError,
    pickle.UnpicklingError,
)


def file_expired(file_path: str | Path, expire_seconds: float | None = 3600) -> bool:
    path = Path(file_path)
    if not path.is_file():
        return True
    if expire_seconds is None or expire_seconds <= 0:
        return False
    return path.stat().st_mtime + float(expire_seconds) < time.time()


def pd_cache(cache_dir: str | Path | None = None, expired: float | None = 0):
    directory = Path(cache_dir or '.pd_cache')
    directory.mkdir(parents=True, exist_ok=True)

    def decorator(func):
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            source = func.__qualname__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = repr((source, args, sorted(kwargs.items()))).encode('utf-8')
            digest = hashlib.sha256(key).hexdigest()[:16]
            cache_file = directory / f'{func.__name__}_{digest}.pkl'

            if not file_expired(cache_file, expired):
                try:
                    cached = pd.read_pickle(cache_file)
                    if isinstance(cached, pd.DataFrame):
                        return cached
                except _CACHE_READ_ERRORS as exc:
                    logger.warning('忽略损坏的 DataFrame 缓存 %s: %s', cache_file, exc)

            frame = func(*args, **kwargs)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError(f'{func.__name__} must return pandas.DataFrame')
            _atomic_pickle(frame, cache_file)
            return frame

        return wrapper

    return decorator


def pd_cached_delete(cache_dir: str | Path | None = None) -> str:
    directory = Path(cache_dir or '.pd_cache')
    cached = list(directory.glob('*.pkl')) if directory.is_dir() else []
    for path in cached:
        path.unlink(missing_ok=True)
    return f'removed {[str(path) for path in cached]}' if cached else 'No cached DataFrames'
