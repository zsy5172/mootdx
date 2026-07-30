from __future__ import annotations

import asyncio

import pytest

from mootdx_next.api.clients import AsyncClient
from mootdx_next.api.clients import SyncClient


class FactorClient(SyncClient):
    def __init__(self) -> None:
        super().__init__()
        self.bar_calls = 0
        self.xdxr_calls = 0

    def bars(self, symbol, frequency=9, start=0, offset=800):
        self.bar_calls += 1
        if start:
            return []
        return [
            _bar("2024-01-02", 10.0),
            _bar("2024-01-03", 12.0),
            _bar("2024-01-04", 11.9),
        ]

    def xdxr(self, symbol):
        self.xdxr_calls += 1
        return [
            {
                "year": 2024,
                "month": 1,
                "day": 4,
                "category": 1,
                "fenhong": 1.0,
                "peigu": 0.0,
                "peigujia": 0.0,
                "songzhuangu": 0.0,
            }
        ]


def _bar(date: str, close: float) -> dict[str, object]:
    return {
        "datetime": f"{date} 15:00",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "vol": 1.0,
        "volume": 1.0,
        "amount": close,
    }


def test_raw_adjustment_factors_return_json_friendly_affine_rows_and_cache() -> None:
    client = FactorClient()

    rows = client.adjustment_factors("600036")
    cached = client.adjustment_factors("600036")

    assert rows == cached
    assert rows[0]["datetime"] == "2024-01-02 15:00"
    assert rows[0]["date"] == "2024-01-02"
    assert rows[0]["previous_close"] is None
    assert rows[1]["qfq_add"] == pytest.approx(-0.1)
    assert rows[2]["hfq_add"] == pytest.approx(0.1)
    assert rows[2]["hfq_close"] == pytest.approx(12.0)
    assert client.bar_calls == 1
    assert client.xdxr_calls == 1


def test_async_raw_adjustment_factors_delegate_to_thread_local_sync_api() -> None:
    sync_client = FactorClient()
    client = AsyncClient(sync_client=sync_client)

    rows = asyncio.run(client.adjustment_factors("600036"))

    assert rows[-1]["hfq_close"] == pytest.approx(12.0)
