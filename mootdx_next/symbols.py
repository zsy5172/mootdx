from __future__ import annotations

from mootdx_next.constants import MARKET_BJ
from mootdx_next.constants import MARKET_SH
from mootdx_next.constants import MARKET_SZ
from mootdx_next.errors import InvalidSymbolError


def _split_symbol(symbol: str) -> tuple[str | None, str]:
    if not isinstance(symbol, str):
        raise InvalidSymbolError("stock code need str type")

    normalized = symbol.strip()
    if not normalized:
        raise InvalidSymbolError("symbol cannot be blank")

    prefix = normalized[:2].lower()
    if prefix not in {"sh", "sz", "bj"}:
        return None, normalized

    bare_symbol = normalized[2:]
    if bare_symbol[:1] in {".", "#"}:
        bare_symbol = bare_symbol[1:]
    if not bare_symbol:
        raise InvalidSymbolError("symbol cannot be blank")
    return prefix, bare_symbol


def get_stock_market(symbol: str, string: bool = False) -> int | str:
    prefix, bare_symbol = _split_symbol(symbol)
    market = prefix or "sh"
    if prefix is not None:
        market = prefix
    elif bare_symbol.startswith(("50", "51", "58", "60", "68", "90", "110", "111", "113", "118", "240")):
        market = "sh"
    elif bare_symbol.startswith(("00", "12", "13", "18", "15", "16", "18", "20", "30", "39", "115")):
        market = "sz"
    elif bare_symbol.startswith(("5", "6", "7", "90", "88", "98", "99")):
        market = "sh"
    elif bare_symbol.startswith(("20", "4", "82", "83", "87", "899", "92")):
        market = "bj"

    if string:
        return market
    if market == "sh":
        return MARKET_SH
    if market == "sz":
        return MARKET_SZ
    return MARKET_BJ


def normalize_symbol(symbol: str) -> str:
    return _split_symbol(symbol)[1]


def get_stock_markets(symbols: list[str]) -> list[tuple[int, str]]:
    if not isinstance(symbols, list):
        raise InvalidSymbolError("stock code need list type")

    return [(get_stock_market(symbol, string=False), normalize_symbol(symbol)) for symbol in symbols]


def normalize_symbol_input(symbol: str | list[str] | None) -> list[str]:
    if symbol is None:
        return []

    if isinstance(symbol, str):
        if not symbol.strip():
            return []
        return [symbol.strip()]

    if isinstance(symbol, list):
        normalized: list[str] = []
        for item in symbol:
            if not isinstance(item, str):
                raise InvalidSymbolError("symbol list must contain only strings")
            if not item.strip():
                continue
            normalized.append(item.strip())
        return normalized

    raise InvalidSymbolError("symbol must be a string, list of strings, or None")
