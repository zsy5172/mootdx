from __future__ import annotations

import json
import re

import httpx
import pandas as pd

from mootdx.cache import file_cache
from mootdx.utils import get_config_path
from mootdx.utils import get_stock_market

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_FACTOR_PAYLOAD = re.compile(r'^[^=]*=(?P<payload>.+?);?\s*$', re.DOTALL)


def _parse_factor_payload(text: str, method: str) -> pd.DataFrame:
    matched = _FACTOR_PAYLOAD.match(text.strip())
    if matched is None:
        raise ValueError(f'新浪 {method} 复权因子响应格式无效')

    try:
        payload = json.loads(matched.group('payload').rstrip(';'))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f'新浪 {method} 复权因子响应格式无效') from exc

    rows = payload.get('data') if isinstance(payload, dict) else None
    if not rows:
        raise ValueError(f'新浪 {method} 复权因子不可用')

    result = pd.DataFrame(rows, columns=['date', 'factor'])
    result['date'] = pd.to_datetime(result['date'], errors='raise')
    result['factor'] = pd.to_numeric(result['factor'], errors='raise')
    return result.set_index('date').sort_index()


def _qualified_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise TypeError('symbol must be a string')

    bare_symbol = symbol.strip().lower()
    for prefix in ('sh', 'sz', 'bj'):
        if bare_symbol.startswith(prefix):
            bare_symbol = bare_symbol[len(prefix):]
            if bare_symbol[:1] in {'.', '#'}:
                bare_symbol = bare_symbol[1:]
            break
    if not bare_symbol:
        raise ValueError('symbol cannot be empty')

    market = get_stock_market(bare_symbol, string=True)
    return f'{market}{bare_symbol}'


def fq_factor(
    symbol: str,
    method: str,
    *,
    http_client: httpx.Client | None = None,
    refresh_time: float = 24 * 3600,
) -> pd.DataFrame:
    normalized_method = str(method).strip().lower()
    if normalized_method not in {'qfq', 'hfq'}:
        raise ValueError("method must be 'qfq' or 'hfq'")

    qualified = _qualified_symbol(symbol)
    cache_file = get_config_path(f'caches/factor/{qualified}_{normalized_method}.plk')

    @file_cache(filepath=cache_file, refresh_time=refresh_time)
    def _factor() -> pd.DataFrame:
        url = f'https://finance.sina.com.cn/realstock/company/{qualified}/{normalized_method}.js'
        if http_client is not None:
            response = http_client.get(url)
            response.raise_for_status()
            return _parse_factor_payload(response.text, normalized_method)

        with httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
        return _parse_factor_payload(response.text, normalized_method)

    return _factor()
