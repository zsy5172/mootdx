from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd

SeriesLike = pd.Series | pd.DataFrame | Iterable[float]


def ref(values: SeriesLike, periods: int = 1, *, source: str = "close") -> pd.Series:
    """Return the value from ``periods`` rows ago."""

    periods = _non_negative_period(periods, "periods")
    result = _series(values, source).shift(periods)
    result.name = f"ref_{periods}"
    return result


def hhv(values: SeriesLike, period: int, *, source: str = "high") -> pd.Series:
    """Rolling highest value over ``period`` rows."""

    period = _positive_period(period)
    result = _series(values, source).rolling(period, min_periods=period).max()
    result.name = f"hhv_{period}"
    return result


def llv(values: SeriesLike, period: int, *, source: str = "low") -> pd.Series:
    """Rolling lowest value over ``period`` rows."""

    period = _positive_period(period)
    result = _series(values, source).rolling(period, min_periods=period).min()
    result.name = f"llv_{period}"
    return result


def ma(values: SeriesLike, period: int, *, source: str = "close") -> pd.Series:
    """Simple moving average with a complete-window warm-up."""

    period = _positive_period(period)
    result = _series(values, source).rolling(period, min_periods=period).mean()
    result.name = f"ma_{period}"
    return result


def ema(
    values: SeriesLike,
    period: int,
    *,
    source: str = "close",
    min_periods: int = 1,
) -> pd.Series:
    """Exponentially weighted moving average using ``adjust=False``."""

    period = _positive_period(period)
    min_periods = _bounded_min_periods(min_periods, period)
    result = _series(values, source).ewm(
        span=period,
        adjust=False,
        min_periods=min_periods,
    ).mean()
    result.name = f"ema_{period}"
    return result


def macd(
    values: SeriesLike,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    source: str = "close",
) -> pd.DataFrame:
    """Return TDX-style DIF, DEA and double-width MACD histogram series."""

    fast = _positive_period(fast, "fast")
    slow = _positive_period(slow, "slow")
    signal = _positive_period(signal, "signal")
    if fast >= slow:
        raise ValueError("fast must be smaller than slow")
    prices = _series(values, source)
    dif = prices.ewm(span=fast, adjust=False, min_periods=1).mean() - prices.ewm(
        span=slow,
        adjust=False,
        min_periods=1,
    ).mean()
    dea = dif.ewm(span=signal, adjust=False, min_periods=1).mean()
    histogram = (dif - dea) * 2.0
    return pd.DataFrame({"dif": dif, "dea": dea, "histogram": histogram}, index=prices.index)


def rsi(values: SeriesLike, period: int = 14, *, source: str = "close") -> pd.Series:
    """Wilder RSI as a floating-point series in the closed interval [0, 100]."""

    period = _positive_period(period)
    prices = _series(values, source)
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = _wilder_average(gain, period)
    average_loss = _wilder_average(loss, period)
    denominator = average_gain + average_loss
    result = 100.0 * average_gain / denominator
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    result = result.mask(denominator == 0, 0.0)
    result.name = f"rsi_{period}"
    return result


def boll(
    values: SeriesLike,
    period: int = 20,
    *,
    deviations: float = 2.0,
    source: str = "close",
) -> pd.DataFrame:
    """Bollinger bands using population standard deviation (``ddof=0``)."""

    period = _positive_period(period)
    width = _positive_number(deviations, "deviations")
    prices = _series(values, source)
    middle = prices.rolling(period, min_periods=period).mean()
    deviation = prices.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {
            "upper": middle + deviation * width,
            "middle": middle,
            "lower": middle - deviation * width,
        },
        index=prices.index,
    )


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder average true range from high, low and close columns."""

    period = _positive_period(period)
    frame = _frame(bars, ("high", "low", "close"))
    high = frame["high"]
    low = frame["low"]
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    result = _wilder_average(true_range, period)
    result.name = f"atr_{period}"
    return result


def vwap(
    bars: pd.DataFrame,
    *,
    window: int | None = None,
    lot_size: int = 100,
    amount: str = "amount",
    volume: str = "volume",
) -> pd.Series:
    """Volume-weighted price from amount in yuan and volume in lots.

    ``window=None`` returns cumulative VWAP. A positive window returns rolling
    VWAP. Zero-volume denominators are represented as ``NaN``.
    """

    lot_size = _positive_period(lot_size, "lot_size")
    frame = _frame(bars, (amount,))
    volume_column = volume if volume in frame.columns else "vol"
    if volume_column not in frame.columns:
        raise ValueError(f"frame is missing required column: {volume}")
    amounts = frame[amount]
    volumes = _numeric_series(frame[volume_column], volume_column)
    if (amounts < 0).any() or (volumes < 0).any():
        raise ValueError("amount and volume must be non-negative")

    if window is None:
        amount_sum = amounts.cumsum()
        volume_sum = volumes.cumsum()
        name = "vwap"
    else:
        window = _positive_period(window, "window")
        amount_sum = amounts.rolling(window, min_periods=window).sum()
        volume_sum = volumes.rolling(window, min_periods=window).sum()
        name = f"vwap_{window}"
    denominator = volume_sum * lot_size
    result = amount_sum / denominator.mask(denominator == 0)
    result.name = name
    return result


def _series(values: SeriesLike, source: str) -> pd.Series:
    if isinstance(values, pd.DataFrame):
        if source not in values.columns:
            raise ValueError(f"frame is missing required column: {source}")
        return _numeric_series(values[source], source)
    if isinstance(values, pd.Series):
        return _numeric_series(values, source)
    try:
        return _numeric_series(pd.Series(list(values), dtype="float64"), source)
    except TypeError as exc:
        raise TypeError("indicator input must be a Series, DataFrame or iterable") from exc


def _frame(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("indicator requires a pandas DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"frame is missing required columns: {', '.join(missing)}")
    result = frame.copy(deep=True)
    for column in columns:
        result[column] = _numeric_series(result[column], column)
    return result


def _numeric_series(values: pd.Series, name: str) -> pd.Series:
    try:
        result = pd.to_numeric(values.copy(deep=True), errors="raise").astype("float64")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc
    finite = result.dropna().map(math.isfinite)
    if not finite.all():
        raise ValueError(f"{name} must contain only finite values or NaN")
    return result


def _wilder_average(values: pd.Series, period: int) -> pd.Series:
    result = pd.Series(float("nan"), index=values.index, dtype="float64")
    seed: list[float] = []
    previous: float | None = None
    for position, value in enumerate(values.to_numpy(dtype="float64", na_value=float("nan"))):
        if math.isnan(value):
            seed = []
            previous = None
            continue
        if previous is None:
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = sum(seed[-period:]) / period
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result.iloc[position] = previous
    return result


def _positive_period(value: int, name: str = "period") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_period(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _bounded_min_periods(value: int, period: int) -> int:
    value = _positive_period(value, "min_periods")
    if value > period:
        raise ValueError("min_periods cannot exceed period")
    return value


def _positive_number(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


__all__ = ["atr", "boll", "ema", "hhv", "llv", "ma", "macd", "ref", "rsi", "vwap"]
