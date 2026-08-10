from __future__ import annotations

from mootdx_next.constants import MARKET_BJ
from mootdx_next.constants import MARKET_SH
from mootdx_next.constants import MARKET_SZ
from mootdx_next.errors import InvalidSymbolError


SH_ETF_PREFIXES = ("50", "51", "52", "53", "56", "58")
SZ_ETF_PREFIXES = ("15", "16", "18")
BSE_STOCK_PREFIX = "920"
BSE_LEGACY_STOCK_PREFIXES = ("43", "82", "83", "87", "88", "89")


def _normalized_market_and_code(symbol: str, market: int | None = None) -> tuple[int, str]:
    prefix, code = _split_symbol(symbol)
    resolved_market = get_stock_market(symbol, string=False) if market is None else int(market)
    if prefix is not None:
        prefixed_market = {"sz": MARKET_SZ, "sh": MARKET_SH, "bj": MARKET_BJ}[prefix]
        if market is not None and prefixed_market != resolved_market:
            raise InvalidSymbolError(f"symbol prefix {prefix} conflicts with market {market}")
        resolved_market = prefixed_market
    return int(resolved_market), code


def is_etf(symbol: str, market: int | None = None) -> bool:
    """Return whether *symbol* uses a known Shanghai/Shenzhen fund code range."""

    resolved_market, code = _normalized_market_and_code(symbol, market)
    if len(code) != 6 or not code.isdigit():
        return False
    if resolved_market == MARKET_SH:
        return code.startswith(SH_ETF_PREFIXES)
    if resolved_market == MARKET_SZ:
        return code.startswith(SZ_ETF_PREFIXES)
    return False


def is_index(symbol: str, market: int | None = None) -> bool:
    """Return whether *symbol* is in a known standard-market index range."""

    resolved_market, code = _normalized_market_and_code(symbol, market)
    if len(code) != 6 or not code.isdigit():
        return False
    if resolved_market == MARKET_SH:
        return code.startswith("000") or code == "999999" or code.startswith("88")
    if resolved_market == MARKET_SZ:
        return code.startswith("399")
    if resolved_market == MARKET_BJ:
        return code.startswith("899")
    return False


def is_stock(symbol: str, market: int | None = None) -> bool:
    """Return whether *symbol* is an A-share code rather than a fund or index."""

    resolved_market, code = _normalized_market_and_code(symbol, market)
    if len(code) != 6 or not code.isdigit():
        return False
    if resolved_market == MARKET_SH:
        return code.startswith(("60", "68"))
    if resolved_market == MARKET_SZ:
        return code.startswith("0") or code.startswith("30")
    if resolved_market == MARKET_BJ:
        return code.startswith((BSE_STOCK_PREFIX, *BSE_LEGACY_STOCK_PREFIXES))
    return False


def get_security_type(market: int, code: str) -> str:
    """Classify a standard-market code for protocol price scaling."""

    code_head = str(code)[:2]
    if market == MARKET_SZ:
        if code_head in {"00", "30"}:
            return "SZ_A_STOCK"
        if code_head == "20":
            return "SZ_B_STOCK"
        if code_head == "39":
            return "SZ_INDEX"
        if str(code).startswith(SZ_ETF_PREFIXES):
            return "SZ_FUND"
        if code_head in {"10", "11", "12", "13", "14"}:
            return "SZ_BOND"
    elif market == MARKET_SH:
        if code_head in {"60", "68"}:
            return "SH_A_STOCK"
        if code_head == "90":
            return "SH_B_STOCK"
        if code_head in {"00", "88", "99"}:
            return "SH_INDEX"
        if str(code).startswith(SH_ETF_PREFIXES):
            return "SH_FUND"
        if code_head in {"01", "10", "11", "12", "13", "14", "20"}:
            return "SH_BOND"
    elif market == MARKET_BJ:
        if str(code).startswith("899"):
            return "BJ_INDEX"
        if str(code).startswith((BSE_STOCK_PREFIX, *BSE_LEGACY_STOCK_PREFIXES)):
            return "BJ_STOCK"
    return "UNKNOWN"


def get_security_coefficient(market: int, code: str) -> float:
    """Return the yuan multiplier for quote/minute/tick integer prices."""

    coefficients = {
        "SH_A_STOCK": 0.01,
        "SH_B_STOCK": 0.001,
        "SH_INDEX": 0.01,
        "SH_FUND": 0.001,
        "SH_BOND": 0.0001,
        "SZ_A_STOCK": 0.01,
        "SZ_B_STOCK": 0.01,
        "SZ_INDEX": 0.01,
        "SZ_FUND": 0.001,
        "SZ_BOND": 0.0001,
        "BJ_STOCK": 0.01,
        "BJ_INDEX": 0.01,
    }
    return coefficients.get(get_security_type(int(market), str(code)), 0.01)


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
    elif bare_symbol.startswith(
        (BSE_STOCK_PREFIX, *BSE_LEGACY_STOCK_PREFIXES, "899")
    ):
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
