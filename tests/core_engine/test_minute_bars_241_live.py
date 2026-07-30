from __future__ import annotations

import os
from collections import Counter
from math import isclose

import pytest

from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from tests.core_engine.support import PREFERRED_HQ_HOSTS

pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_LIVE_241") != "1",
    reason="set MOOTDX_RUN_LIVE_241=1 to run the 241-minute live matrix",
)


def _servers() -> list[ServerEndpoint]:
    return [
        ServerEndpoint(host=host, port=port, label=label)
        for label, host, port in PREFERRED_HQ_HOSTS
    ]


@pytest.mark.network
def test_live_241_minute_bars_conserve_each_opening_bar() -> None:
    client = SyncClient(servers=_servers(), max_retries=2)
    try:
        raw = client.bars("600036", frequency=8, start=0, offset=800)
        rebuilt = client.minute_bars_241("600036", start=0, offset=800)
    finally:
        client.close()

    raw_counts = Counter(str(row["datetime"])[:10] for row in raw)
    rebuilt_counts = Counter(str(row["datetime"])[:10] for row in rebuilt)
    verified_days = 0
    complete_days = 0
    for day, raw_count in raw_counts.items():
        source = next(
            (row for row in raw if str(row["datetime"]) == f"{day} 09:31"),
            None,
        )
        auction = next(
            (row for row in rebuilt if str(row["datetime"]) == f"{day} 09:30"),
            None,
        )
        regular = next(
            (row for row in rebuilt if str(row["datetime"]) == f"{day} 09:31"),
            None,
        )
        if source is None or auction is None or regular is None:
            continue

        verified_days += 1
        assert rebuilt_counts[day] == raw_count + 1
        assert auction["is_call_auction"] is True
        assert regular["auction_adjusted"] is True
        assert isclose(
            float(source["volume"]),
            float(auction["volume"]) + float(regular["volume"]),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        assert isclose(
            float(source["amount"]),
            float(auction["amount"]) + float(regular["amount"]),
            rel_tol=0.0,
            abs_tol=0.01,
        )
        if raw_count == 240:
            complete_days += 1
            assert rebuilt_counts[day] == 241

    assert verified_days >= 1
    assert complete_days >= 1
