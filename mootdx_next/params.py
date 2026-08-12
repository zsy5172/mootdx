from __future__ import annotations

from datetime import datetime

from mootdx_next.errors import InvalidDateError
from mootdx_next.errors import InvalidFrequencyError

FREQUENCY_ALIASES = {
    "5m": 0,
    "15m": 1,
    "30m": 2,
    "1h": 3,
    "days": 4,
    "week": 5,
    "mon": 6,
    "ex_1m": 7,
    "1m": 8,
    "day": 9,
    "dk": 9,
    "3mon": 10,
    "year": 11,
}

VALID_FREQUENCIES = set(range(12))


def normalize_frequency(frequency: int | str) -> int:
    # ``bool`` is an ``int`` subclass, but accepting True/False here silently
    # maps to the 15-minute/5-minute wire values and is almost certainly a
    # caller bug.
    if isinstance(frequency, bool):
        raise InvalidFrequencyError(f"unsupported frequency type: {type(frequency).__name__}")

    if isinstance(frequency, int):
        if frequency in VALID_FREQUENCIES:
            return frequency
        raise InvalidFrequencyError(f"unsupported frequency: {frequency}")

    if isinstance(frequency, str):
        normalized = FREQUENCY_ALIASES.get(frequency.lower())
        if normalized is None:
            raise InvalidFrequencyError(f"unsupported frequency: {frequency}")
        return normalized

    raise InvalidFrequencyError(f"unsupported frequency type: {type(frequency).__name__}")


def normalize_date(value: str | int) -> str:
    if isinstance(value, int):
        value = str(value)

    if not isinstance(value, str):
        raise InvalidDateError(f"unsupported date type: {type(value).__name__}")

    raw = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y%m%d")
        except ValueError:
            pass

    raise InvalidDateError(f"invalid date: {value}")


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")
