from __future__ import annotations

from datetime import datetime


def is_trading_session(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    minute_of_day = current.hour * 60 + current.minute

    if 9 * 60 + 30 <= minute_of_day < 11 * 60 + 30:
        return True

    if 13 * 60 <= minute_of_day < 15 * 60:
        return True

    return False
