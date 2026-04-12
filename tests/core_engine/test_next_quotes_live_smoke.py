from __future__ import annotations

import pytest

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


@pytest.mark.network
def test_next_quotes_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.quotes(["600036", "000001"])
    finally:
        client.connection_pool.close_all()

    assert rows
    assert [row["code"] for row in rows] == ["600036", "000001"]
    assert {"market", "code", "price", "servertime", "vol"} <= set(rows[0])
