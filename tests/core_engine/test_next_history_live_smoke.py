from __future__ import annotations

import pytest

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


@pytest.mark.network
def test_next_bars_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.bars(symbol="600036", frequency="day", offset=10)
    finally:
        client.connection_pool.close_all()

    assert rows
    assert len(rows) == 10
    assert {"open", "close", "datetime", "vol", "volume"} <= set(rows[0])


@pytest.mark.network
def test_next_minutes_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.minutes(symbol="000001", date="20171010")
    finally:
        client.connection_pool.close_all()

    assert rows
    assert {"price", "vol", "volume"} <= set(rows[0])


@pytest.mark.network
def test_next_minute_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        minute_rows = client.minute(symbol="000001")
        latest_rows = client.latest_minutes(symbol="000001")
    finally:
        client.connection_pool.close_all()

    assert isinstance(minute_rows, list)
    assert latest_rows
    assert latest_rows[0]["price"] > 0
