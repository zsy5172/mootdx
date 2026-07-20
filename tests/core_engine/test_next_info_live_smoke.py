from __future__ import annotations

import pytest

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


@pytest.mark.network
def test_next_finance_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        row = client.finance(symbol="000001")
    finally:
        client.connection_pool.close_all()

    assert row["code"] == "000001"
    assert "liutongguben" in row


@pytest.mark.network
def test_next_xdxr_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.xdxr(symbol="600036")
    finally:
        client.connection_pool.close_all()

    assert rows
    assert {"year", "month", "day", "category", "name"} <= set(rows[0])
