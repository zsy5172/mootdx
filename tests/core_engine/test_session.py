from __future__ import annotations

from datetime import datetime

from mootdx_next.session import is_trading_session


def test_trading_session_boundaries() -> None:
    assert not is_trading_session(datetime(2026, 4, 10, 9, 29))
    assert not is_trading_session(datetime(2026, 4, 10, 9, 30))
    assert is_trading_session(datetime(2026, 4, 10, 9, 31))
    assert is_trading_session(datetime(2026, 4, 10, 11, 29))
    assert not is_trading_session(datetime(2026, 4, 10, 11, 30))
    assert not is_trading_session(datetime(2026, 4, 10, 13, 0))
    assert is_trading_session(datetime(2026, 4, 10, 13, 1))
    assert is_trading_session(datetime(2026, 4, 10, 14, 59))
    assert not is_trading_session(datetime(2026, 4, 10, 15, 0))
