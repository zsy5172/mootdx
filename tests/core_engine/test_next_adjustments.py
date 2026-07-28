from __future__ import annotations

import asyncio
import warnings

import pandas as pd
import pandas.testing as pdt
import pytest

from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextStdQuotes
from mootdx_next import AsyncPandasClient
from mootdx_next import PandasClient
from mootdx_next.adjustments import AdjustmentService
from mootdx_next.errors import AdjustmentError


def _daily_frame(
    dates: list[str],
    *,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    close_values = closes or [float(value) for value in range(10, 10 + len(dates))]
    rows = []
    for date, close in zip(dates, close_values, strict=True):
        timestamp = pd.Timestamp(date).replace(hour=15)
        rows.append(
            {
                'open': close - 0.5,
                'close': close,
                'high': close + 1,
                'low': close - 1,
                'vol': 100.0,
                'amount': 1000.0,
                'year': timestamp.year,
                'month': timestamp.month,
                'day': timestamp.day,
                'hour': timestamp.hour,
                'minute': timestamp.minute,
                'datetime': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'volume': 100.0,
            }
        )
    return pd.DataFrame.from_records(rows)


class AdjustmentFixtureClient:
    def __init__(self, daily: pd.DataFrame, xdxr: list[dict[str, object]]) -> None:
        self.daily = daily.copy()
        self.xdxr_rows = xdxr
        self.bars_calls: list[tuple[str, int, int, int]] = []
        self.xdxr_calls: list[str] = []
        self.index_calls = 0
        self.closed = False

    def bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800):
        self.bars_calls.append((symbol, frequency, start, offset))
        if frequency != 9:
            return []

        stop = max(0, len(self.daily) - start)
        begin = max(0, stop - offset)
        return self.daily.iloc[begin:stop].to_dict('records')

    def xdxr(self, symbol: str):
        self.xdxr_calls.append(symbol)
        return list(self.xdxr_rows)

    def minutes(self, symbol: str, date: str):
        return [{'price': 10.0, 'vol': 100.0, 'volume': 100.0}]

    def index_bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800, market=None):
        self.index_calls += 1
        return self.daily.iloc[-1:].to_dict('records')

    def close(self) -> None:
        self.closed = True


class AsyncAdjustmentFixtureClient:
    def __init__(self, daily: pd.DataFrame, xdxr: list[dict[str, object]]) -> None:
        self.sync = AdjustmentFixtureClient(daily, xdxr)

    async def bars(self, *args, **kwargs):
        return self.sync.bars(*args, **kwargs)

    async def xdxr(self, *args, **kwargs):
        return self.sync.xdxr(*args, **kwargs)

    async def minutes(self, *args, **kwargs):
        return self.sync.minutes(*args, **kwargs)

    def close(self) -> None:
        self.sync.close()


def _cash_event(date: str, **values: float) -> dict[str, object]:
    timestamp = pd.Timestamp(date)
    return {
        'year': timestamp.year,
        'month': timestamp.month,
        'day': timestamp.day,
        'category': 1,
        'fenhong': values.get('fenhong', 1.0),
        'peigu': values.get('peigu', 0.0),
        'peigujia': values.get('peigujia', 0.0),
        'songzhuangu': values.get('songzhuangu', 0.0),
    }


def _suogu_event(date: str, suogu: float | None) -> dict[str, object]:
    timestamp = pd.Timestamp(date)
    return {
        'year': timestamp.year,
        'month': timestamp.month,
        'day': timestamp.day,
        'category': 11,
        'suogu': suogu,
        'd': date,
        'f': '1',
        's': str(suogu),
        'u': 'fixture',
    }


def _non_tradable_contraction_event(date: str, suogu: float) -> dict[str, object]:
    event = _suogu_event(date, suogu)
    event['category'] = 12
    return event


def _warrant_event(
    date: str,
    *,
    category: int = 14,
    fenshu: float = 6.0,
    xingquanjia: float = 5.65,
) -> dict[str, object]:
    timestamp = pd.Timestamp(date)
    return {
        'year': timestamp.year,
        'month': timestamp.month,
        'day': timestamp.day,
        'category': category,
        'fenshu': fenshu,
        'xingquanjia': xingquanjia,
    }


def _service(
    daily: pd.DataFrame,
    events: list[dict[str, object]],
    *,
    page_size: int = 800,
) -> tuple[AdjustmentService, AdjustmentFixtureClient]:
    client = AdjustmentFixtureClient(daily, events)
    return AdjustmentService(client, page_size=page_size, cache_ttl=3600), client


def test_cash_dividend_qfq_and_hfq_match_tdx_formula() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05'],
        closes=[10.0, 12.0, 11.9, 13.0],
    )
    service, _ = _service(daily, [_cash_event('2024-01-04')], page_size=2)

    qfq = service.adjusted_daily('600036', 'qfq')
    hfq = service.adjusted_daily('600036', 'hfq')

    ratio = 119 / 120
    before = pd.Timestamp('2024-01-03 15:00:00')
    event_date = pd.Timestamp('2024-01-04 15:00:00')
    assert qfq.at[before, 'close'] == pytest.approx(12 * ratio)
    assert qfq.at[event_date, 'close'] == pytest.approx(11.9)
    assert hfq.at[before, 'close'] == pytest.approx(12)
    assert hfq.at[event_date, 'close'] == pytest.approx(11.9 / ratio)
    assert qfq.at[before, 'factor'] == pytest.approx(ratio)
    assert hfq.at[event_date, 'factor'] == pytest.approx(1 / ratio)


def test_etf_suogu_uses_tdx_factor_once_without_sina_shape_assumptions() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05'],
        closes=[9.0, 10.0, 8.4, 8.8],
    )
    service, _ = _service(daily, [_suogu_event('2024-01-04', 1.2)])

    qfq = service.adjusted_daily('510500', 'qfq')
    hfq = service.adjusted_daily('510500', 'hfq')

    before = pd.Timestamp('2024-01-03 15:00:00')
    event_date = pd.Timestamp('2024-01-04 15:00:00')
    assert qfq.at[before, 'close'] == pytest.approx(10 / 1.2)
    assert qfq.at[event_date, 'close'] == pytest.approx(8.4)
    assert hfq.at[before, 'close'] == pytest.approx(10)
    assert hfq.at[event_date, 'close'] == pytest.approx(8.4 * 1.2)


def test_etf_cash_distributions_use_reversible_affine_adjustment() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 8.4, 8.3],
    )
    events = [_suogu_event('2024-01-03', 1.2), _cash_event('2024-01-04', fenhong=1.0)]
    service, _ = _service(daily, events)

    qfq = service.adjusted_daily('510500', 'qfq')
    hfq = service.adjusted_daily('510500', 'hfq')

    first = pd.Timestamp('2024-01-02 15:00:00')
    latest = pd.Timestamp('2024-01-04 15:00:00')
    assert qfq.at[first, 'close'] == pytest.approx(10 / 1.2 - 0.1)
    assert hfq.at[latest, 'close'] == pytest.approx((8.3 + 0.1) * 1.2)


@pytest.mark.parametrize('symbol', ['161725', '508000', '180101'])
def test_fund_cash_distributions_use_affine_adjustment_without_a_split_event(
    symbol: str,
) -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[1.5, 1.4, 1.39],
    )
    service, _ = _service(daily, [_cash_event('2024-01-04', fenhong=0.5)])

    qfq = service.adjusted_daily(symbol, 'qfq')
    hfq = service.adjusted_daily(symbol, 'hfq')

    before = pd.Timestamp('2024-01-03 15:00:00')
    event_date = pd.Timestamp('2024-01-04 15:00:00')
    assert qfq.at[before, 'close'] == pytest.approx(1.4 - 0.05)
    assert hfq.at[event_date, 'close'] == pytest.approx(1.39 + 0.05)


def test_non_tradable_share_contraction_does_not_adjust_tradable_share_prices() -> None:
    daily = _daily_frame(
        ['2005-11-18', '2005-12-30', '2006-01-05'],
        closes=[2.99, 3.23, 3.20],
    )
    service, _ = _service(
        daily,
        [_non_tradable_contraction_event('2005-12-30', 0.58)],
    )

    qfq = service.adjusted_daily('000523', 'qfq')
    hfq = service.adjusted_daily('000523', 'hfq')

    pdt.assert_series_equal(qfq['close'], daily.set_index(pd.to_datetime(daily['datetime']))['close'])
    pdt.assert_series_equal(hfq['close'], qfq['close'])
    assert (qfq['factor'] == 1).all()
    assert (hfq['factor'] == 1).all()


@pytest.mark.parametrize(('category', 'name'), [(13, '认购'), (14, '认沽')])
def test_warrant_distribution_fails_instead_of_silently_returning_wrong_adjustment(
    category: int,
    name: str,
) -> None:
    daily = _daily_frame(
        ['2006-01-11', '2006-02-27', '2006-02-28'],
        closes=[7.68, 6.66, 6.69],
    )
    events = [
        _cash_event('2006-02-27', fenhong=0, songzhuangu=2.596299886703491),
        _warrant_event('2006-02-27', category=category),
    ]
    service, _ = _service(daily, events)

    with pytest.raises(
        AdjustmentError,
        match=rf'category {category}.*{name}权证.*2006-02-27.*估值',
    ):
        service.adjusted_daily('600036', 'qfq')


def test_warrant_error_only_blocks_ranges_that_depend_on_the_unknown_value() -> None:
    daily = _daily_frame(
        ['2006-01-11', '2006-02-27', '2006-02-28'],
        closes=[7.68, 6.66, 6.69],
    )
    service, _ = _service(daily, [_warrant_event('2006-02-27')])

    recent_qfq = service.adjusted_range(
        '600036',
        'qfq',
        start='2006-02-27',
        end='2006-02-28',
    )
    old_hfq = service.adjusted_range(
        '600036',
        'hfq',
        start='2006-01-11',
        end='2006-01-11',
    )

    assert recent_qfq['close'].tolist() == [6.66, 6.69]
    assert old_hfq['close'].tolist() == [7.68]
    with pytest.raises(AdjustmentError, match='category 14'):
        service.adjusted_range('600036', 'qfq', start='2006-01-11', end='2006-01-11')
    with pytest.raises(AdjustmentError, match='category 14'):
        service.adjusted_range('600036', 'hfq', start='2006-02-27', end='2006-02-28')


def test_combined_cash_rights_and_stock_dividend_event() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[18.0, 20.0, 15.7],
    )
    event = _cash_event(
        '2024-01-04',
        fenhong=2.0,
        peigu=1.0,
        peigujia=5.0,
        songzhuangu=2.0,
    )
    service, _ = _service(daily, [event])

    qfq = service.adjusted_daily('600036', 'qfq')

    expected_ratio = ((20 * 10 - 2 + 1 * 5) / (10 + 1 + 2)) / 20
    assert qfq.at[pd.Timestamp('2024-01-03 15:00:00'), 'close'] == pytest.approx(20 * expected_ratio)


@pytest.mark.parametrize('symbol', ['600036', 'sh600036', 'SH600036', 'Sh600036', ' 600036 ', ' sh600036 '])
def test_symbol_forms_share_one_canonical_adjustment_snapshot(symbol: str) -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 12.0, 11.9],
    )
    service, client = _service(daily, [_cash_event('2024-01-04')])

    expected = service.adjusted_daily('600036', 'qfq')
    actual = service.adjusted_daily(symbol, 'qfq')

    pdt.assert_frame_equal(actual, expected)
    assert client.xdxr_calls == ['sh600036']


def test_explicit_market_prefix_is_preserved_for_ambiguous_code() -> None:
    daily = _daily_frame(['2024-01-02'])
    service, client = _service(daily, [])

    service.adjusted_daily('SH000001', 'qfq')

    assert client.bars_calls[0][0] == 'sh000001'
    assert client.xdxr_calls == ['sh000001']


@pytest.mark.parametrize('symbol', ['510500', 'sh510500', 'SH510500', ' 510500 '])
def test_etf_detection_does_not_depend_on_symbol_prefix_form(symbol: str) -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 8.4, 8.3],
    )
    events = [_suogu_event('2024-01-03', 1.2), _cash_event('2024-01-04', fenhong=1.0)]
    service, _ = _service(daily, events)

    result = service.adjusted_daily(symbol, 'qfq')

    assert result.at[pd.Timestamp('2024-01-02 15:00:00'), 'close'] == pytest.approx(10 / 1.2 - 0.1)


def test_bj_adjustment_uses_market_two_bars_and_xdxr() -> None:
    client = AdjustmentFixtureClient(
        _daily_frame(
            ['2024-01-02', '2024-01-03'],
            closes=[10.0, 9.9],
        ),
        [_cash_event('2024-01-03')],
    )
    facade = NextStdQuotes(engine_client=client)

    result = facade.bars('BJ920001', adjust='qfq')

    assert result.iloc[0]['close'] == pytest.approx(9.9)
    assert client.bars_calls[-1][0] == 'bj920001'
    assert client.xdxr_calls == ['bj920001']


def test_events_before_the_first_available_bar_do_not_change_the_adjustment_baseline() -> None:
    daily = _daily_frame(
        ['2022-12-27', '2023-06-13', '2023-06-14'],
        closes=[10.0, 12.0, 11.8],
    )
    service, _ = _service(
        daily,
        [
            _cash_event('2019-06-17', fenhong=1.77),
            _cash_event('2023-06-14', fenhong=2.0),
        ],
    )

    qfq = service.adjusted_daily('BJ920001', 'qfq')
    hfq = service.adjusted_daily('BJ920001', 'hfq')

    expected_ratio = (12.0 - 0.2) / 12.0
    assert qfq.iloc[0]['factor'] == pytest.approx(expected_ratio)
    assert hfq.iloc[0]['factor'] == pytest.approx(1)
    assert hfq.iloc[-1]['factor'] == pytest.approx(1 / expected_ratio)


def test_daily_window_is_identical_regardless_of_requested_offset() -> None:
    dates = list(pd.bdate_range('2024-01-02', periods=10).strftime('%Y-%m-%d'))
    daily = _daily_frame(dates)
    service, _ = _service(daily, [_cash_event(dates[-2])], page_size=3)

    wide = service.adjusted_bars('600036', 'qfq', frequency=9, start=0, offset=3)
    window = service.adjusted_bars('600036', 'qfq', frequency=9, start=1, offset=2)

    pdt.assert_frame_equal(window, wide.loc[window.index])


@pytest.mark.parametrize(
    ('frequency', 'period'),
    [(5, 'W-FRI'), (6, 'M'), (10, 'Q-DEC'), (11, 'Y-DEC')],
)
def test_period_bars_equal_adjusted_daily_then_aggregated(frequency: int, period: str) -> None:
    dates = list(pd.bdate_range('2024-01-02', '2024-02-09').strftime('%Y-%m-%d'))
    closes = [float(value) for value in range(20, 20 + len(dates))]
    daily = _daily_frame(dates, closes=closes)
    service, _ = _service(daily, [_cash_event('2024-01-18', fenhong=2.0)])

    adjusted_daily = service.adjusted_daily('600036', 'qfq')
    actual = service.adjusted_bars('600036', 'qfq', frequency=frequency, start=0, offset=800)
    groups = adjusted_daily.groupby(adjusted_daily.index.to_period(period))
    expected = pd.DataFrame(
        {
            'open': groups['open'].first(),
            'high': groups['high'].max(),
            'low': groups['low'].min(),
            'close': groups['close'].last(),
        }
    )
    expected.index = pd.DatetimeIndex(groups.apply(lambda group: group.index[-1]).to_numpy())
    expected.index.name = 'datetime'

    pdt.assert_frame_equal(
        actual[['open', 'high', 'low', 'close']],
        expected[['open', 'high', 'low', 'close']],
        check_freq=False,
    )


@pytest.mark.parametrize('frequency', [5, 6, 10, 11])
def test_recent_period_qfq_does_not_require_an_older_unvalued_warrant(
    frequency: int,
) -> None:
    dates = list(pd.bdate_range('2006-01-09', '2007-03-09').strftime('%Y-%m-%d'))
    daily = _daily_frame(dates)
    service, _ = _service(daily, [_warrant_event('2006-02-27')])

    result = service.adjusted_bars(
        '600036',
        'qfq',
        frequency=frequency,
        start=0,
        offset=1,
    )

    assert len(result) == 1
    assert result.iloc[-1]['factor'] == 1


def test_long_suspension_uses_the_last_trading_close_before_the_event() -> None:
    daily = _daily_frame(
        ['2006-01-11', '2006-02-27', '2006-02-28'],
        closes=[7.68, 7.20, 7.25],
    )
    service, _ = _service(daily, [_cash_event('2006-01-20', fenhong=1.0)])

    qfq = service.adjusted_daily('600036', 'qfq')
    hfq = service.adjusted_daily('600036', 'hfq')

    ratio = (7.68 - 0.1) / 7.68
    assert qfq.loc[pd.Timestamp('2006-01-11 15:00:00'), 'close'] == pytest.approx(7.68 * ratio)
    assert qfq.loc[pd.Timestamp('2006-02-27 15:00:00'), 'close'] == pytest.approx(7.20)
    assert hfq.loc[pd.Timestamp('2006-02-27 15:00:00'), 'close'] == pytest.approx(7.20 / ratio)


@pytest.mark.parametrize('date', ['20240103', '2024-01-03', 20240103])
def test_minutes_adjust_real_price_shape_for_the_requested_trading_date(date: str | int) -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 12.0, 11.9],
    )
    client = AdjustmentFixtureClient(daily, [_cash_event('2024-01-04')])
    facade = NextStdQuotes(engine_client=client)

    result = facade.minutes('SH600036', date=date, adjust='qfq')

    assert result.iloc[0]['price'] == pytest.approx(10 * 119 / 120)
    assert result.iloc[0]['factor'] == pytest.approx(119 / 120)
    assert result.iloc[0]['vol'] == 100
    assert result.iloc[0]['volume'] == 100
    assert isinstance(result.index, pd.RangeIndex)


def test_get_k_data_only_checks_warrant_events_needed_by_requested_range() -> None:
    daily = _daily_frame(
        ['2006-01-11', '2006-02-27', '2006-02-28'],
        closes=[7.68, 6.66, 6.69],
    )
    client = PandasClient(raw_client=AdjustmentFixtureClient(daily, [_warrant_event('2006-02-27')]))

    result = client.get_k_data(
        '600036',
        start_date='2006-02-27',
        end_date='2006-02-28',
        adjust='qfq',
    )

    assert result['close'].tolist() == [6.66, 6.69]


def test_async_minutes_and_history_adjustments_match_sync_range_semantics() -> None:
    async def run() -> None:
        daily = _daily_frame(
            ['2006-01-11', '2006-02-27', '2006-02-28', '2024-01-02', '2024-01-03', '2024-01-04'],
            closes=[7.68, 6.66, 6.69, 10.0, 12.0, 11.9],
        )
        events = [
            _warrant_event('2006-02-27'),
            _cash_event('2024-01-04'),
        ]
        sync_client = PandasClient(raw_client=AdjustmentFixtureClient(daily, events))
        async_client = AsyncPandasClient(raw_client=AsyncAdjustmentFixtureClient(daily, events))

        sync_minutes = sync_client.minutes('600036', date=20240103, adjust='qfq')
        async_minutes = await async_client.minutes('600036', date=20240103, adjust='qfq')
        pdt.assert_frame_equal(async_minutes, sync_minutes)

        sync_history = sync_client.get_k_data(
            '600036',
            start_date='2006-02-27',
            end_date='2006-02-28',
            adjust='qfq',
        )
        async_history = await async_client.get_k_data(
            '600036',
            start_date='2006-02-27',
            end_date='2006-02-28',
            adjust='qfq',
        )
        pdt.assert_frame_equal(async_history, sync_history)

    asyncio.run(run())


def test_k_and_ohlc_use_next_adjustment_without_legacy_factor_lookup() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 12.0, 11.9],
    )
    client = AdjustmentFixtureClient(daily, [_cash_event('2024-01-04')])
    facade = NextStdQuotes(engine_client=client)

    k_data = facade.k('SH600036', begin='2024-01-02', end='2024-01-04', adjust='qfq')
    ohlc = facade.ohlc(symbol=' sh600036 ', begin='2024-01-02', end='2024-01-04', adjust='qfq')

    assert k_data.loc['2024-01-03', 'close'] == pytest.approx(12 * 119 / 120)
    pdt.assert_series_equal(k_data['close'], ohlc['close'])
    assert client.xdxr_calls == ['sh600036']


def test_adjustment_is_sorted_and_warning_free_for_future_pandas_join_semantics() -> None:
    daily = _daily_frame(
        ['2024-01-04', '2024-01-02', '2024-01-03'],
        closes=[11.9, 10.0, 12.0],
    )
    service, _ = _service(daily, [_cash_event('2024-01-04')])

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        result = service.adjusted_daily('600036', 'qfq')

    assert result.index.is_monotonic_increasing
    assert not result[['open', 'high', 'low', 'close', 'factor']].isna().any().any()


def test_invalid_etf_factor_raises_instead_of_returning_raw_prices() -> None:
    daily = _daily_frame(['2024-01-02', '2024-01-03'])
    service, fixture_client = _service(daily, [_suogu_event('2024-01-03', None)])

    with pytest.raises(AdjustmentError, match='missing numeric'):
        service.adjusted_daily('510500', 'qfq')

    facade = NextStdQuotes(engine_client=fixture_client)
    with pytest.raises(MootdxValidationException, match='missing numeric'):
        facade.bars('510500', adjust='qfq')


def test_index_bars_ignores_adjust_compatibility_keyword() -> None:
    client = AdjustmentFixtureClient(_daily_frame(['2024-01-02']), [])
    facade = NextStdQuotes(engine_client=client)

    result = facade.index_bars('000001', market=1, adjust='qfq')

    assert result.empty is False
    assert client.index_calls == 1
    assert client.xdxr_calls == []
