from __future__ import annotations

import asyncio
import os

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient


pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_TRANSACTION_HISTORY_LIVE") != "1",
    reason="set MOOTDX_RUN_TRANSACTION_HISTORY_LIVE=1 to run live TDX history checks",
)


def test_live_transaction_history_infers_listing_month_and_keeps_async_parity() -> None:
    symbol = "600036"
    before = "20020531"
    sync_client = SyncClient()
    try:
        date, rows = next(
            sync_client.iter_transaction_history(
                symbol,
                before=before,
                page_size=2000,
                max_pages=1,
            )
        )
    finally:
        sync_client.close()

    assert "20020401" <= date <= before
    assert rows
    assert all(str(row["datetime"]).startswith(date[:4] + "-" + date[4:6] + "-" + date[6:]) for row in rows)
    assert all(float(row["price"]) > 0 for row in rows)

    async def first_async_chunk():
        client = AsyncClient()
        try:
            return await anext(
                client.iter_transaction_history(
                    symbol,
                    before=date,
                    page_size=2000,
                    max_pages=1,
                )
            )
        finally:
            client.close()

    async_date, async_rows = asyncio.run(first_async_chunk())
    assert async_date == date
    assert async_rows == rows
