from __future__ import annotations

import pytest

from mootdx.quotes import Quotes

COMPAT_SERVER = ("175.178.128.227", 7709)


@pytest.mark.network
def test_quotes_compat_live_smoke_quotes_and_finance() -> None:
    client = Quotes.factory(market="std", engine="next", server=COMPAT_SERVER, timeout=5)
    try:
        quotes = client.quotes(symbol="600036")
        finance = client.finance(symbol="000001")
    finally:
        client.close()

    assert quotes.empty is False
    assert finance.empty is False


@pytest.mark.network
def test_quotes_compat_live_smoke_history_apis() -> None:
    client = Quotes.factory(market="std", engine="next", server=COMPAT_SERVER, timeout=5)
    try:
        bars = client.bars(symbol="600036", frequency=9, offset=10)
        index_data = client.index(symbol="000001", frequency=9, start=1, offset=2, market=1)
        minutes = client.minutes(symbol="000001", date="20171010")
        transactions = client.transactions(symbol="600036", date="20170209", offset=10)
        xdxr = client.xdxr(symbol="600036")
        k_data = client.k(symbol="600036", begin="2019-07-03", end="2019-07-10")
        block_data = client.block()
    finally:
        client.close()

    assert bars.empty is False
    assert index_data.empty is False
    assert minutes.empty is False
    assert transactions.empty is False
    assert xdxr.empty is False
    assert "code" in k_data.columns
    assert block_data.empty is False
