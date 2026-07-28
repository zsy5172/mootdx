from __future__ import annotations

import asyncio
import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from mootdx_next.constants import MARKET_BJ
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
ADJUSTABLE_PRICE_COLUMNS = (*PRICE_COLUMNS, 'price')
SZ_FUND_PREFIXES = ('15', '16', '18')
SH_FUND_PREFIXES = ('50', '51', '52', '56', '588', '589')


class AdjustmentClient(Protocol):
    def bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]: ...

    def xdxr(self, symbol: str) -> list[dict[str, object]]: ...


class AsyncAdjustmentClient(Protocol):
    async def bars(
        self,
        symbol: str,
        frequency: int | str = 9,
        start: int = 0,
        offset: int = 800,
    ) -> list[dict[str, object]]: ...

    async def xdxr(self, symbol: str) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class _AdjustmentSnapshot:
    daily: pd.DataFrame
    events: tuple[tuple[pd.Timestamp, float], ...]
    actions: tuple['_CorporateAction', ...]
    uses_affine_adjustment: bool
    unresolved_events: tuple['_UnresolvedEvent', ...]
    loaded_at: float


@dataclass(frozen=True)
class _CorporateAction:
    date: pd.Timestamp
    scale: float
    offset: float


@dataclass(frozen=True)
class _UnresolvedEvent:
    date: pd.Timestamp
    category: int
    label: str
    reason: str


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

        method = self._require_adjustment(adjust)
        snapshot = self._snapshot(symbol)
        window = self._select_bars_window(snapshot.daily, frequency=frequency, start=start, offset=offset)
        adjusted = self._apply_snapshot(window, snapshot, method)
        if frequency == DAILY_FREQUENCY:
            return adjusted
        return self._aggregate(adjusted, frequency)

    def adjusted_range(
        self,
        symbol: str,
        adjust: str,
        *,
        start: object,
        end: object,
    ) -> pd.DataFrame:
        method = self._require_adjustment(adjust)
        snapshot = self._snapshot(symbol)
        window = self._slice_date_range(snapshot.daily, start=start, end=end)
        return self._apply_snapshot(window, snapshot, method)

    def apply(
        self,
        frame: pd.DataFrame,
        symbol: str,
        adjust: str,
        *,
        as_of: object | None = None,
    ) -> pd.DataFrame:
        method = self._require_adjustment(adjust)
        if frame.empty:
            return frame.copy()
        snapshot = self._snapshot(symbol)
        return self._apply_snapshot(frame, snapshot, method, as_of=as_of)

    def _snapshot(self, symbol: str) -> _AdjustmentSnapshot:
        canonical = self._canonical_symbol(symbol)
        now = time.monotonic()

        with self._lock:
            cached = self._cache.get(canonical)
            if cached is not None and now - cached.loaded_at < self._cache_ttl:
                return cached

            daily = self._load_daily(canonical)
            rows = self._client.xdxr(canonical)
            events, actions, uses_affine_adjustment, unresolved = self._build_events(
                daily,
                rows,
                symbol=canonical,
            )
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
        *,
        symbol: str | None = None,
    ) -> tuple[
        tuple[tuple[pd.Timestamp, float], ...],
        tuple[_CorporateAction, ...],
        bool,
        tuple[_UnresolvedEvent, ...],
    ]:
        daily_dates = daily.index.normalize()
        first_daily_date = daily_dates.min()
        # Funds use affine cash/split actions. A category-11 record is also
        # sufficient evidence even when its split predates the bar window.
        uses_affine_adjustment = cls._is_fund_symbol(symbol) or any(
            cls._integer(row.get('category')) == 11 for row in rows
        )
        ratios: dict[pd.Timestamp, float] = {}
        actions: list[_CorporateAction] = []
        unresolved: list[_UnresolvedEvent] = []

        for row in rows:
            category = cls._integer(row.get('category'))
            if category == 12:
                # Category 12 only contracts non-tradable shares. Audited TDX
                # records do not change the per-share price of tradable holders.
                continue
            if category in {13, 14}:
                event_date = cls._event_date(row)
                if event_date <= first_daily_date:
                    continue
                warrant_name = '认购权证' if category == 13 else '认沽权证'
                unresolved.append(
                    _UnresolvedEvent(
                        date=event_date,
                        category=category,
                        label=warrant_name,
                        reason='缺少复权所需估值',
                    )
                )
                continue
            if category not in {1, 11}:
                continue

            event_date = cls._event_date(row)
            if event_date <= first_daily_date:
                # TDX may return pre-listing or pre-renumbering events for which
                # this symbol has no event-before bars. The first available bar
                # is the adjustment baseline, so those events cannot affect it.
                continue
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
                    unresolved.append(
                        _UnresolvedEvent(
                            date=event_date,
                            category=category,
                            label='除权除息',
                            reason='缺少事件日前一交易日收盘价',
                        )
                    )
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
        ordered_unresolved = tuple(
            sorted(set(unresolved), key=lambda item: (item.date, item.category, item.label, item.reason))
        )
        return events, ordered_actions, uses_affine_adjustment, ordered_unresolved

    @classmethod
    def _apply_snapshot(
        cls,
        frame: pd.DataFrame,
        snapshot: _AdjustmentSnapshot,
        adjust: str,
        *,
        as_of: object | None = None,
    ) -> pd.DataFrame:
        if as_of is None:
            result = cls._to_datetime_frame(frame)
            target_dates = result.index.normalize()
        else:
            result = frame.copy()
            target_date = cls._normalize_target_date(as_of)
            target_dates = pd.DatetimeIndex([target_date] * len(result))
        if result.empty:
            return result

        cls._raise_for_unresolved(snapshot.unresolved_events, target_dates, adjust)

        if snapshot.uses_affine_adjustment:
            factor, offset = cls._affine_parameters(snapshot.actions, result.index, target_dates, adjust)
            for column in ADJUSTABLE_PRICE_COLUMNS:
                if column in result.columns:
                    result[column] = (
                        pd.to_numeric(result[column], errors='coerce') * factor.to_numpy() + offset.to_numpy()
                    )
            result['factor'] = factor.to_numpy()
        else:
            factor = pd.Series(1.0, index=result.index)
            for event_date, ratio in snapshot.events:
                if adjust == 'qfq':
                    mask = target_dates < event_date
                    factor = factor.where(~mask, factor * ratio)
                else:
                    mask = target_dates >= event_date
                    factor = factor.where(~mask, factor / ratio)

            for column in ADJUSTABLE_PRICE_COLUMNS:
                if column in result.columns:
                    result[column] = pd.to_numeric(result[column], errors='coerce') * factor.to_numpy()
            result['factor'] = factor.to_numpy()

        return result.sort_index() if as_of is None else result

    @staticmethod
    def _affine_parameters(
        actions: tuple[_CorporateAction, ...],
        index: pd.Index,
        target_dates: pd.DatetimeIndex,
        adjust: str,
    ) -> tuple[pd.Series, pd.Series]:
        factor = pd.Series(1.0, index=index)
        offset = pd.Series(0.0, index=index)

        if adjust == 'qfq':
            for action in actions:
                mask = target_dates < action.date
                factor = factor.where(~mask, action.scale * factor)
                offset = offset.where(~mask, action.scale * offset + action.offset)
        else:
            # Forward adjustment composes actions in chronological order.
            # Its inverse must therefore undo those actions in reverse order.
            for action in reversed(actions):
                mask = target_dates >= action.date
                factor = factor.where(~mask, factor / action.scale)
                offset = offset.where(~mask, (offset - action.offset) / action.scale)

        return factor, offset

    @staticmethod
    def _raise_for_unresolved(
        unresolved: tuple[_UnresolvedEvent, ...],
        target_dates: pd.DatetimeIndex,
        adjust: str,
    ) -> None:
        for event in unresolved:
            required = (target_dates < event.date).any() if adjust == 'qfq' else (target_dates >= event.date).any()
            if required:
                raise AdjustmentError(
                    f'cannot calculate {adjust}: category {event.category} {event.label}，'
                    f'事件日期 {event.date.date()}，{event.reason}'
                )

    @classmethod
    def _select_bars_window(
        cls,
        daily: pd.DataFrame,
        *,
        frequency: int,
        start: int,
        offset: int,
    ) -> pd.DataFrame:
        if frequency == DAILY_FREQUENCY:
            return cls._slice_latest(daily, start=start, offset=offset)

        period_alias = cls._period_alias(frequency)
        period_bars = cls._aggregate(daily, frequency)
        selected = cls._slice_latest(period_bars, start=start, offset=offset)
        if selected.empty:
            return daily.iloc[0:0].copy()

        selected_periods = selected.index.to_period(period_alias)
        daily_periods = daily.index.to_period(period_alias)
        return daily.loc[daily_periods.isin(selected_periods)].copy()

    @staticmethod
    def _slice_date_range(frame: pd.DataFrame, *, start: object, end: object) -> pd.DataFrame:
        start_date = AdjustmentService._normalize_target_date(start)
        end_date = AdjustmentService._normalize_target_date(end)
        if end_date < start_date:
            return frame.iloc[0:0].copy()
        dates = frame.index.normalize()
        return frame.loc[(dates >= start_date) & (dates <= end_date)].copy()

    @classmethod
    def _aggregate(cls, daily: pd.DataFrame, frequency: int) -> pd.DataFrame:
        period_alias = cls._period_alias(frequency)
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
    def _period_alias(frequency: int) -> str:
        return {
            5: 'W-FRI',
            6: 'M',
            10: 'Q-DEC',
            11: 'Y-DEC',
        }[frequency]

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
    def _normalize_target_date(value: object) -> pd.Timestamp:
        normalized = str(value) if isinstance(value, int) else value
        try:
            timestamp = pd.Timestamp(normalized)
        except (TypeError, ValueError) as exc:
            raise AdjustmentError(f'invalid adjustment date: {value}') from exc
        if pd.isna(timestamp):
            raise AdjustmentError(f'invalid adjustment date: {value}')
        return timestamp.normalize()

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
        if market not in {MARKET_SH, MARKET_SZ, MARKET_BJ}:
            raise UnsupportedMarketError('price adjustment only supports sh/sz/bj securities')
        prefix = {
            MARKET_SH: 'sh',
            MARKET_SZ: 'sz',
            MARKET_BJ: 'bj',
        }[market]
        return f'{prefix}{normalize_symbol(raw)}'

    @staticmethod
    def _is_fund_symbol(symbol: str | None) -> bool:
        if not symbol:
            return False
        normalized = symbol.strip().lower()
        if normalized.startswith('sz'):
            return normalize_symbol(normalized).startswith(SZ_FUND_PREFIXES)
        if normalized.startswith('sh'):
            return normalize_symbol(normalized).startswith(SH_FUND_PREFIXES)
        return False


class AsyncAdjustmentService:
    """Async adjustment service with an independent cache and awaited raw I/O."""

    def __init__(
        self,
        client: AsyncAdjustmentClient,
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
        self._lock = asyncio.Lock()

    async def invalidate(self, symbol: str | None = None) -> None:
        async with self._lock:
            if symbol is None:
                self._cache.clear()
                return
            self._cache.pop(AdjustmentService._canonical_symbol(symbol), None)

    async def adjusted_daily(self, symbol: str, adjust: str) -> pd.DataFrame:
        method = AdjustmentService._require_adjustment(adjust)
        snapshot = await self._snapshot(symbol)
        return AdjustmentService._apply_snapshot(snapshot.daily, snapshot, method)

    async def adjusted_bars(
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

        method = AdjustmentService._require_adjustment(adjust)
        snapshot = await self._snapshot(symbol)
        window = AdjustmentService._select_bars_window(
            snapshot.daily,
            frequency=frequency,
            start=start,
            offset=offset,
        )
        adjusted = AdjustmentService._apply_snapshot(window, snapshot, method)
        if frequency == DAILY_FREQUENCY:
            return adjusted
        return AdjustmentService._aggregate(adjusted, frequency)

    async def adjusted_range(
        self,
        symbol: str,
        adjust: str,
        *,
        start: object,
        end: object,
    ) -> pd.DataFrame:
        method = AdjustmentService._require_adjustment(adjust)
        snapshot = await self._snapshot(symbol)
        window = AdjustmentService._slice_date_range(snapshot.daily, start=start, end=end)
        return AdjustmentService._apply_snapshot(window, snapshot, method)

    async def apply(
        self,
        frame: pd.DataFrame,
        symbol: str,
        adjust: str,
        *,
        as_of: object | None = None,
    ) -> pd.DataFrame:
        method = AdjustmentService._require_adjustment(adjust)
        if frame.empty:
            return frame.copy()
        snapshot = await self._snapshot(symbol)
        return AdjustmentService._apply_snapshot(frame, snapshot, method, as_of=as_of)

    async def _snapshot(self, symbol: str) -> _AdjustmentSnapshot:
        canonical = AdjustmentService._canonical_symbol(symbol)
        now = time.monotonic()
        cached = self._cache.get(canonical)
        if cached is not None and now - cached.loaded_at < self._cache_ttl:
            return cached

        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(canonical)
            if cached is not None and now - cached.loaded_at < self._cache_ttl:
                return cached

            daily = await self._load_daily(canonical)
            rows = await self._client.xdxr(canonical)
            events, actions, uses_affine_adjustment, unresolved = AdjustmentService._build_events(
                daily,
                rows,
                symbol=canonical,
            )
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

    async def _load_daily(self, symbol: str) -> pd.DataFrame:
        pages: list[pd.DataFrame] = []
        previous_oldest: pd.Timestamp | None = None

        for page_number in range(self._max_pages):
            rows = await self._client.bars(
                symbol=symbol,
                frequency=DAILY_FREQUENCY,
                start=page_number * self._page_size,
                offset=self._page_size,
            )
            if not rows:
                break

            page = AdjustmentService._to_datetime_frame(pd.DataFrame.from_records(rows))
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
