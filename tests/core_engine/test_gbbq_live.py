from __future__ import annotations

import asyncio
import os

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient
from mootdx_next.gbbq import GbbqHttpProvider
from mootdx_next.gbbq import GbbqRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_GBBQ_LIVE") != "1",
    reason="set MOOTDX_RUN_GBBQ_LIVE=1 to download and verify the official GBBQ snapshot",
)


def test_official_gbbq_snapshot_and_tdx_symbol_response_agree() -> None:
    registry = GbbqRegistry()
    client = SyncClient(gbbq_registry=registry, gbbq_provider=GbbqHttpProvider())

    all_rows = client.gbbq_all(refresh=True)
    assert len(all_rows) > 200_000
    assert {0, 1, 2} <= {int(row["market"]) for row in all_rows}

    official = client.gbbq("sh600036", fallback=False)
    wire = client.xdxr("sh600036")
    assert official
    assert wire

    official_keys = {
        (row["date"], row["category"])
        for row in official
    }
    wire_keys = {
        (str(row["datetime"])[:10], row["category"])
        for row in wire
    }
    assert len(official_keys & wire_keys) >= min(len(official_keys), len(wire_keys)) * 0.95

    async_rows = asyncio.run(AsyncClient(sync_client=client).gbbq("600036", fallback=False))
    assert async_rows == official
