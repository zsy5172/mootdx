from __future__ import annotations

import functools
import os
import pickle
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec
from typing import TypeAlias

import pandas as pd

P = ParamSpec('P')
PathLike: TypeAlias = str | Path

_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_CACHE_READ_ERRORS = (
    AttributeError,
    EOFError,
    ImportError,
    OSError,
    ValueError,
    pickle.UnpicklingError,
)


def _path_lock(path: Path) -> threading.RLock:
    key = path.absolute()
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _atomic_pickle(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(exist_ok=True, parents=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f'.{path.name}.',
            suffix='.tmp',
            dir=path.parent,
            delete=False,
        ) as output:
            temporary = Path(output.name)
        frame.to_pickle(temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_cache(filepath: PathLike, refresh_time: float | None = None):
    """Cache a DataFrame result and rebuild missing, expired, or corrupt files."""

    path = Path(filepath)
    lock = _path_lock(path)

    def decorator(func: Callable[P, pd.DataFrame]):
        @functools.wraps(func)
        def retrieve_cache(*args: P.args, **kwargs: P.kwargs) -> pd.DataFrame:
            with lock:
                try:
                    fresh = refresh_time is None or path.stat().st_mtime + float(refresh_time) >= time.time()
                    if fresh:
                        cached = pd.read_pickle(path)
                        if isinstance(cached, pd.DataFrame):
                            return cached
                except _CACHE_READ_ERRORS:
                    pass

                dataframe = func(*args, **kwargs)
                if not isinstance(dataframe, pd.DataFrame):
                    raise TypeError(f'{func.__name__} must return pandas.DataFrame')
                _atomic_pickle(dataframe, path)
                return dataframe

        return retrieve_cache

    return decorator
