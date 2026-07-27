from __future__ import annotations

import httpx
import pandas as pd
import pandas.testing as pdt
import pytest

import mootdx.utils.factor as factor_module
import mootdx.utils.adjust as adjust_module
from mootdx.utils.adjust import fq_factor as compatibility_factor
from mootdx.utils.adjust import get_xdxr
from mootdx.utils.factor import _parse_factor_payload
from mootdx.utils.factor import fq_factor

PAYLOAD = 'var factor={"data":[["2024-01-03","1.25"],["2024-01-02","1.00"]]};'
LIVE_STYLE_PAYLOAD = """
var sh600036qfq={"total":2,"data":[{"d":"2024-01-03","f":"1.25"},{"d":"2024-01-02","f":"1.00"}]};
/* published by Sina */
"""


def test_parse_factor_payload_uses_json_and_normalizes_types() -> None:
    result = _parse_factor_payload(PAYLOAD, 'qfq')

    assert list(result.index) == [pd.Timestamp('2024-01-02'), pd.Timestamp('2024-01-03')]
    assert result['factor'].tolist() == [1.0, 1.25]


def test_parse_factor_payload_accepts_live_object_rows_and_trailing_comment() -> None:
    result = _parse_factor_payload(LIVE_STYLE_PAYLOAD, 'qfq')

    assert list(result.index) == [pd.Timestamp('2024-01-02'), pd.Timestamp('2024-01-03')]
    assert result['factor'].tolist() == [1.0, 1.25]


@pytest.mark.parametrize(
    'payload',
    [
        'not an assignment',
        'var factor=__import__("os").system("false");',
        'var factor={"data":[]};',
        'var factor={"data":[["2024-01-02","1.0"]]};alert("unexpected");',
    ],
)
def test_parse_factor_payload_rejects_invalid_or_executable_content(payload: str) -> None:
    with pytest.raises(ValueError, match='复权因子'):
        _parse_factor_payload(payload, 'qfq')


def test_fq_factor_checks_status_and_uses_method_specific_cache(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=PAYLOAD)

    monkeypatch.setattr(factor_module, 'get_config_path', lambda value: str(tmp_path / value))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = fq_factor('SH600036', 'qfq', http_client=client)
        second = fq_factor('600036', 'qfq', http_client=client)
        hfq = fq_factor('600036', 'hfq', http_client=client)

    pdt.assert_frame_equal(first, second)
    pdt.assert_frame_equal(first, hfq)
    assert len(calls) == 2
    assert calls[0].endswith('/sh600036/qfq.js')
    assert calls[1].endswith('/sh600036/hfq.js')


def test_fq_factor_propagates_http_status_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(factor_module, 'get_config_path', lambda value: str(tmp_path / value))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            fq_factor('600036', 'qfq', http_client=client)


def test_compatibility_factor_keeps_legacy_frame_shape(monkeypatch) -> None:
    source = pd.DataFrame(
        {'factor': [1.0, 1.25]},
        index=pd.to_datetime(['2024-01-02', '2024-01-03']),
    )
    monkeypatch.setattr('mootdx.utils.adjust._factor', lambda symbol, method: source)

    result = compatibility_factor('qfq', '600036')

    assert list(result.columns) == ['date', 'qfq_factor']
    assert result['qfq_factor'].tolist() == [1.0, 1.25]


def test_get_xdxr_closes_an_owned_quotes_client(tmp_path, monkeypatch) -> None:
    class FakeQuotes:
        closed = False

        def xdxr(self, symbol: str) -> pd.DataFrame:
            return pd.DataFrame([{'year': 2024, 'month': 1, 'day': 2}])

        def close(self) -> None:
            self.closed = True

    client = FakeQuotes()
    monkeypatch.setattr(adjust_module, 'get_config_path', lambda value: str(tmp_path / value))
    monkeypatch.setattr(adjust_module.Quotes, 'factory', lambda *args, **kwargs: client)

    result = get_xdxr('600036')

    assert client.closed
    assert result.index.equals(pd.DatetimeIndex(['2024-01-02'], name='date'))


@pytest.mark.parametrize('method', ['before', '01', '', 'invalid'])
def test_fq_factor_rejects_unknown_method(method: str) -> None:
    with pytest.raises(ValueError, match='qfq.*hfq'):
        fq_factor('600036', method)
