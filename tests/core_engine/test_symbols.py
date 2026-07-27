from __future__ import annotations

import pytest

from mootdx.consts import MARKET_BJ
from mootdx.consts import MARKET_SH
from mootdx.consts import MARKET_SZ
from mootdx.utils import get_stock_market as legacy_stock_market
from mootdx.utils import get_stock_markets as legacy_stock_markets
from mootdx.utils import normalize_stock_symbol
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.symbols import get_stock_market
from mootdx_next.symbols import get_stock_markets
from mootdx_next.symbols import normalize_symbol


@pytest.mark.parametrize(
    ('symbol', 'market', 'code'),
    [
        ('600036', MARKET_SH, '600036'),
        ('SH600036', MARKET_SH, '600036'),
        ('SH.600036', MARKET_SH, '600036'),
        ('sh#600036', MARKET_SH, '600036'),
        ('000001', MARKET_SZ, '000001'),
        ('SZ.000001', MARKET_SZ, '000001'),
        ('920001', MARKET_BJ, '920001'),
        ('BJ.920001', MARKET_BJ, '920001'),
        ('899001', MARKET_BJ, '899001'),
        ('880001', MARKET_SH, '880001'),
        ('111001', MARKET_SH, '111001'),
        ('118001', MARKET_SH, '118001'),
        ('240001', MARKET_SH, '240001'),
        ('700001', MARKET_SH, '700001'),
    ],
)
def test_legacy_and_next_symbol_rules_remain_consistent(symbol: str, market: int, code: str) -> None:
    assert get_stock_market(symbol) == market
    assert normalize_symbol(symbol) == code
    assert get_stock_markets([symbol]) == [(market, code)]

    assert legacy_stock_market(symbol) == market
    assert normalize_stock_symbol(symbol) == code
    assert legacy_stock_markets([symbol]) == [[market, code]]


@pytest.mark.parametrize('symbol', ['', ' ', 'SH.', 'BJ#'])
def test_next_symbol_normalization_rejects_blank_codes(symbol: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(symbol)


def test_symbol_market_prefix_overrides_number_inference() -> None:
    assert get_stock_market('SZ.600036') == MARKET_SZ
    assert get_stock_market('BJ.600036') == MARKET_BJ
    assert legacy_stock_market('SZ.600036') == MARKET_SZ
    assert legacy_stock_market('BJ.600036') == MARKET_BJ
