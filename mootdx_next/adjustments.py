from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from mootdx_next.constants import MARKET_SH
from mootdx_next.constants import MARKET_SZ
from mootdx_next.errors import AdjustmentError
from mootdx_next.errors import UnsupportedMarketError
from mootdx_next.symbols import get_stock_market
from mootdx_next.symbols import normalize_symbol

ADJUSTMENT_ALIASES = {
    '01': 'qfq',
    '02': 'hfq',
    'after': 'hfq',
    'before': 'qfq',
    'hfq': 'hfq',
    'qfq': 'qfq',
}

AGGREGATED_FREQUENCIES = {5, 6, 10, 11}
DAILY_FREQUENCY = 9
PRICE_COLUMNS = ('open', 'high', 'low', 'close')


class AdjustmentClient(Protocol):
    def bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]: ...

    def xdxr(self, symbol: str) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class _AdjustmentSnapshot:
    daily: pd.DataFrame
    events: tuple[tuple[pd.Timestamp, float], ...]
    actions: tuple['_CorporateAction', ...]
    uses_affine_adjustment: bool
    unresolved_events: tuple[pd.Timestamp, ...]
    loaded_at: float


@dataclass(frozen=True)
class _CorporateAction:
    date: pd.Timestamp
    scale: float
    offset: float


def normalize_adjustment(adjust: object) -> str | None:
    if not isinstance(adjust, str):
        return None
    return ADJUSTMENT_ALIASES.get(adjust.strip().lower())


class AdjustmentService:
    """Build next-engine adjustment factors from TDX daily bars and xdxr rows."""

    def __init__(
        self,
        client: AdjustmentClient,
        *,
        cache_ttl: float = 3600,
        page_size: int = 800,
        max_pages: int = 100,
    ) -> None:
        self._client = client
        self._cache_ttl = float(cache_ttl)
        self._page_size = int(page_size)
        self._max_pages = int(max_pages)
        self._cache: dict[str, _AdjustmentSnapshot] = {}
        self._lock = threading.RLock()

    def invalidate(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self._cache.clear()
                return
            self._cache.pop(self._canonical_symbol(symbol), None)

    def adjusted_daily(self, symbol: str, adjust: str) -> pd.DataFrame:
        method = self._require_adjustment(adjust)
        snapshot = self._snapshot(symbol)
        return self._apply_snapshot(snapshot.daily, snapshot, method)

    def adjusted_bars(
        self,
        symbol: str,
        adjust: str,
        *,
        frequency: int,
        start: int,
        offset: int,
    ) -> pd.DataFrame:
        if start < 0:
            raise ValueError('start must be >= 0')
        if offset <= 0 or offset > self._page_size:
            raise ValueError(f'offset must be between 1 and {self._page_size}')
        if frequency not in AGGREGATED_FREQUENCIES | {DAILY_FREQUENCY}:
            raise AdjustmentError(f'frequency {frequency} must be adjusted from the requested bars')

        adjusted = self.adjusted_daily(symbol, adjust)
        if frequency in AGGREGATED_FREQUENCIES:
            adjusted = self._aggregate(adjusted, frequency)
        return self._slice_latest(adjusted, start=start, offset=offset)

    def apply(self, frame: pd.DataFrame, symbol: str, adjust: str) -> pd.DataFrame:
        method = self._require_adjustment(adjust)
        if frame.empty:
            return frame.copy()
        snapshot = self._snapshot(symbol)
        return self._apply_snapshot(frame, snapshot, method)

    def _snapshot(self, symbol: str) -> _AdjustmentSnapshot:
        canonical = self._canonical_symbol(symbol)
        now = time.monotonic()

        with self._lock:
            cached = self._cache.get(canonical)
            if cached is not None and now - cached.loaded_at < self._cache_ttl:
                return cached

            daily = self._load_daily(canonical)
            rows = self._client.xdxr(canonical)
            events, actions, uses_affine_adjustment, unresolved = self._build_events(daily, rows)
            snapshot = _AdjustmentSnapshot(
                daily=daily,
                events=events,
                actions=actions,
                uses_affine_adjustment=uses_affine_adjustment,
                unresolved_events=unresolved,
                loaded_at=time.monotonic(),
            )
            self._cache[canonical] = snapshot
            return snapshot

    def _load_daily(self, symbol: str) -> pd.DataFrame:
        pages: list[pd.DataFrame] = []
        previous_oldest: pd.Timestamp | None = None

        for page_number in range(self._max_pages):
            rows = self._client.bars(
                symbol=symbol,
                frequency=DAILY_FREQUENCY,
                start=page_number * self._page_size,
                offset=self._page_size,
            )
            if not rows:
                break

            page = self._to_datetime_frame(pd.DataFrame.from_records(rows))
            if page.empty:
                break
            pages.append(page)

            oldest = page.index.min()
            if len(rows) < self._page_size:
                break
            if previous_oldest is not None and oldest >= previous_oldest:
                break
            previous_oldest = oldest

        if not pages:
            raise AdjustmentError(f'cannot adjust {symbol}: daily bars are empty')

        daily = pd.concat(pages, axis=0, sort=False)
        daily = daily.loc[~daily.index.duplicated(keep='last')].sort_index()
        missing = [column for column in PRICE_COLUMNS if column not in daily.columns]
        if missing:
            raise AdjustmentError(f'cannot adjust {symbol}: daily bars are missing {", ".join(missing)}')
        if 'vol' in daily.columns and 'volume' not in daily.columns:
            daily['volume'] = daily['vol'].to_numpy(copy=False)
        return daily

    @classmethod
    def _build_events(
        cls,
        daily: pd.DataFrame,
        rows: list[dict[str, object]],
    ) -> tuple[
        tuple[tuple[pd.Timestamp, float], ...],
        tuple[_CorporateAction, ...],
        bool,
        tuple[pd.Timestamp, ...],
    ]:
        uses_affine_adjustment = any(cls._integer(row.get('category')) == 11 for row in rows)
        ratios: dict[pd.Timestamp, float] = {}
        actions: list[_CorporateAction] = []
        unresolved: list[pd.Timestamp] = []
        daily_dates = daily.index.normalize()

        for row in rows:
            category = cls._integer(row.get('category'))
            if category not in {1, 11}:
                continue

            event_date = cls._event_date(row)
            ratio: float | None
            if category == 1:
                fenhong = cls._number(row.get('fenhong'), default=0)
                peigu = cls._number(row.get('peigu'), default=0)
                peigujia = cls._number(row.get('peigujia'), default=0)
                songzhuangu = cls._number(row.get('songzhuangu'), default=0)
                denominator = 10 + peigu + songzhuangu
                if denominator <= 0:
                    raise AdjustmentError(f'invalid xdxr values on {event_date.date()}')

                action_scale = 10 / denominator
                action_offset = (-fenhong + peigu * peigujia) / denominator
                actions.append(_CorporateAction(event_date, action_scale, action_offset))
                if uses_affine_adjustment:
                    continue

                previous = daily.loc[daily_dates < event_date, 'close']
                if previous.empty:
                    unresolved.append(event_date)
                    continue

                previous_close = cls._number(previous.iloc[-1])
                if previous_close <= 0:
                    raise AdjustmentError(f'invalid xdxr values on {event_date.date()}')
                ex_reference = action_scale * previous_close + action_offset
                ratio = ex_reference / previous_close
            else:
                suogu = cls._number(row.get('suogu'))
                if suogu <= 0:
                    raise AdjustmentError(f'invalid suogu value on {event_date.date()}')
                actions.append(_CorporateAction(event_date, 1 / suogu, 0))
                continue

            if not math.isfinite(ratio) or ratio <= 0:
                raise AdjustmentError(f'invalid adjustment ratio on {event_date.date()}')
            ratios[event_date] = ratios.get(event_date, 1.0) * ratio

        events = tuple(sorted(ratios.items(), key=lambda item: item[0]))
        ordered_actions = tuple(sorted(actions, key=lambda item: item.date))
        return events, ordered_actions, uses_affine_adjustment, tuple(sorted(set(unresolved)))

    @classmethod
    def _apply_snapshot(
        cls,
        frame: pd.DataFrame,
        snapshot: _AdjustmentSnapshot,
        adjust: str,
    ) -> pd.DataFrame:
        result = cls._to_datetime_frame(frame)
        if result.empty:
            return result

        target_dates = result.index.normalize()
        if snapshot.uses_affine_adjustment:
            factor, offset = cls._affine_parameters(snapshot.actions, result.index, target_dates, adjust)
            for column in PRICE_COLUMNS:
                if column in result.columns:
                    result[column] = (
                        pd.to_numeric(result[column], errors='coerce') * factor.to_numpy() + offset.to_numpy()
                    )
            result['factor'] = factor.to_numpy()
            return result.sort_index()

        cls._raise_for_unresolved(snapshot.unresolved_events, target_dates, adjust)

        factor = pd.Series(1.0, index=result.index)
        for event_date, ratio in snapshot.events:
            if adjust == 'qfq':
                mask = target_dates < event_date
                factor = factor.where(~mask, factor * ratio)
            else:
                mask = target_dates >= event_date
                factor = factor.where(~mask, factor / ratio)

        for column in PRICE_COLUMNS:
            if column in result.columns:
                result[column] = pd.to_numeric(result[column], errors='coerce') * factor.to_numpy()
        result['factor'] = factor.to_numpy()
        return result.sort_index()

    @staticmethod
    def _affine_parameters(
        actions: tuple[_CorporateAction, ...],
        index: pd.DatetimeIndex,
        target_dates: pd.DatetimeIndex,
        adjust: str,
    ) -> tuple[pd.Series, pd.Series]:
        factor = pd.Series(1.0, index=index)
        offset = pd.Series(0.0, index=index)

        for action in actions:
            if adjust == 'qfq':
                mask = target_dates < action.date
                factor = factor.where(~mask, action.scale * factor)
                offset = offset.where(~mask, action.scale * offset + action.offset)
            else:
                mask = target_dates >= action.date
                factor = factor.where(~mask, factor / action.scale)
                offset = offset.where(~mask, (offset - action.offset) / action.scale)

        return factor, offset

    @staticmethod
    def _raise_for_unresolved(
        unresolved: tuple[pd.Timestamp, ...],
        target_dates: pd.DatetimeIndex,
        adjust: str,
    ) -> None:
        for event_date in unresolved:
            required = (target_dates < event_date).any() if adjust == 'qfq' else (target_dates >= event_date).any()
            if required:
                raise AdjustmentError(
                    f'cannot calculate {adjust}: no previous close before {event_date.date()}'
                )

    @classmethod
    def _aggregate(cls, daily: pd.DataFrame, frequency: int) -> pd.DataFrame:
        period_alias = {
            5: 'W-FRI',
            6: 'M',
            10: 'Q-DEC',
            11: 'Y-DEC',
        }[frequency]
        periods = daily.index.to_period(period_alias)
        aggregations: dict[str, str] = {}

        for column in daily.columns:
            if column == 'open':
                aggregations[column] = 'first'
            elif column == 'high':
                aggregations[column] = 'max'
            elif column == 'low':
                aggregations[column] = 'min'
            elif column == 'close':
                aggregations[column] = 'last'
            elif column in {'vol', 'volume', 'amount'}:
                aggregations[column] = 'sum'
            else:
                aggregations[column] = 'last'

        aggregated = daily.groupby(periods, sort=True).agg(aggregations)
        if 'datetime' in aggregated.columns:
            aggregated.index = pd.to_datetime(aggregated['datetime'])
        else:
            date_values = pd.Series(daily.index, index=daily.index)
            last_dates = date_values.groupby(periods, sort=True).last()
            aggregated.index = pd.DatetimeIndex(last_dates.to_numpy())

        dates = aggregated.index
        if 'year' in aggregated.columns:
            aggregated['year'] = dates.year
        if 'month' in aggregated.columns:
            aggregated['month'] = dates.month
        if 'day' in aggregated.columns:
            aggregated['day'] = dates.day
        if 'hour' in aggregated.columns:
            aggregated['hour'] = dates.hour
        if 'minute' in aggregated.columns:
            aggregated['minute'] = dates.minute
        return aggregated.sort_index()

    @staticmethod
    def _slice_latest(frame: pd.DataFrame, *, start: int, offset: int) -> pd.DataFrame:
        stop = max(0, len(frame) - start)
        begin = max(0, stop - offset)
        return frame.iloc[begin:stop].copy()

    @staticmethod
    def _to_datetime_frame(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        if 'datetime' in result.columns:
            index = pd.to_datetime(result['datetime'], errors='coerce')
        elif 'date' in result.columns:
            index = pd.to_datetime(result['date'], errors='coerce')
        elif isinstance(result.index, pd.DatetimeIndex):
            index = pd.DatetimeIndex(result.index)
        else:
            raise AdjustmentError('price data has no datetime information')

        valid = ~pd.isna(index)
        result = result.loc[valid].copy()
        result.index = pd.DatetimeIndex(index[valid])
        return result.sort_index()

    @staticmethod
    def _event_date(row: dict[str, object]) -> pd.Timestamp:
        try:
            value = pd.Timestamp(
                year=int(row['year']),
                month=int(row['month']),
                day=int(row['day']),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdjustmentError('xdxr row has an invalid date') from exc
        return value.normalize()

    @staticmethod
    def _number(value: object, *, default: float | None = None) -> float:
        if value is None or pd.isna(value):
            if default is not None:
                return default
            raise AdjustmentError('xdxr row has a missing numeric value')
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise AdjustmentError(f'invalid numeric value: {value}') from exc
        if not math.isfinite(result):
            raise AdjustmentError(f'invalid numeric value: {value}')
        return result

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _require_adjustment(adjust: str) -> str:
        normalized = normalize_adjustment(adjust)
        if normalized is None:
            raise AdjustmentError(f'unsupported adjustment: {adjust}')
        return normalized

    @staticmethod
    def _canonical_symbol(symbol: str) -> str:
        raw = symbol.strip() if isinstance(symbol, str) else symbol
        market = get_stock_market(raw, string=False)
        if market not in {MARKET_SH, MARKET_SZ}:
            raise UnsupportedMarketError('price adjustment only supports sh/sz securities')
        prefix = 'sh' if market == MARKET_SH else 'sz'
        return f'{prefix}{normalize_symbol(raw)}'
