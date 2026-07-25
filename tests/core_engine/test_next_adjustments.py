from __future__ import annotations

import warnings

import pandas as pd
import pandas.testing as pdt
import pytest

from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextStdQuotes
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
        return [
            {'date': f'{date} 09:30:00', 'price': 10.0, 'open': 10.0, 'high': 10.0, 'low': 10.0, 'close': 10.0}
        ]

    def index_bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800, market=None):
        self.index_calls += 1
        return self.daily.iloc[-1:].to_dict('records')

    def close(self) -> None:
        self.closed = True


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


def test_etf_cash_distributions_use_affine_sina_compatible_adjustment() -> None:
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
    assert hfq.at[latest, 'close'] == pytest.approx(8.3 * 1.2 + 0.1)


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


def test_bj_adjustment_fails_explicitly_in_next_facade() -> None:
    client = AdjustmentFixtureClient(_daily_frame(['2024-01-02']), [])
    facade = NextStdQuotes(engine_client=client)

    with pytest.raises(MootdxValidationException, match='only supports sh/sz'):
        facade.bars('BJ430090', adjust='qfq')


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


def test_minutes_use_the_factor_for_their_trading_date() -> None:
    daily = _daily_frame(
        ['2024-01-02', '2024-01-03', '2024-01-04'],
        closes=[10.0, 12.0, 11.9],
    )
    client = AdjustmentFixtureClient(daily, [_cash_event('2024-01-04')])
    facade = NextStdQuotes(engine_client=client)

    result = facade.minutes('SH600036', date='20240103', adjust='qfq')

    assert result.iloc[0]['close'] == pytest.approx(10 * 119 / 120)
    assert result.iloc[0]['factor'] == pytest.approx(119 / 120)


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
