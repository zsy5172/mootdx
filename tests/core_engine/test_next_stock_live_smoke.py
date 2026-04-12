from __future__ import annotations

import pytest

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


@pytest.mark.network
def test_next_stock_count_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        count = client.stock_count(1)
    finally:
        client.connection_pool.close_all()

    assert count > 1000


@pytest.mark.network
def test_next_stocks_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        count = client.stock_count(1)
        rows = client.stocks(1)
    finally:
        client.connection_pool.close_all()

    assert rows
    assert len(rows) == count
    assert {"code", "volunit", "decimal_point", "name", "pre_close"} <= set(rows[0])
