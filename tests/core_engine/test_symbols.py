from __future__ import annotations

import pytest

from mootdx.consts import MARKET_BJ
from mootdx.consts import MARKET_SH
from mootdx.consts import MARKET_SZ
from mootdx.utils import get_stock_market as legacy_stock_market
from mootdx.utils import get_stock_markets as legacy_stock_markets
from mootdx.utils import normalize_stock_symbol
from mootdx_next.errors import InvalidSymbolError
from mootdx_next.symbols import get_security_coefficient
from mootdx_next.symbols import get_stock_market
from mootdx_next.symbols import get_stock_markets
from mootdx_next.symbols import is_etf
from mootdx_next.symbols import is_index
from mootdx_next.symbols import is_stock
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


@pytest.mark.parametrize("code", ["sh510300", "sh520000", "sh530000", "sh560000", "sh588000", "sz159915"])
def test_security_classification_recognizes_etf_ranges(code: str) -> None:
    assert is_etf(code)
    assert not is_stock(code)


def test_security_classification_distinguishes_stocks_and_indexes() -> None:
    assert is_stock("sh600036")
    assert is_stock("sz000001")
    assert is_stock("bj920786")
    assert is_index("sh000001")
    assert is_index("sz399001")
    assert is_index("bj899050")


def test_security_price_coefficient_uses_three_decimals_for_etfs() -> None:
    assert get_security_coefficient(1, "588000") == 0.001
    assert get_security_coefficient(0, "159915") == 0.001
    assert get_security_coefficient(1, "600036") == 0.01
