from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest

from compat.common import build_artifact
from compat.common import write_json
from compat.comparators import compare_payloads
from compat.registry import build_live_decode_artifact_pair
from mootdx.quotes import Quotes
from mootdx_next import AsyncClient
from mootdx_next import PandasClient
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from mootdx_next import minutes_to_frame
from mootdx_next import transaction_to_frame
from mootdx_next.session import is_trading_session
from tests.core_engine.support import PREFERRED_HQ_HOSTS

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "compat" / "artifacts"
SYMBOL = "600036"
TRANSACTION_WINDOWS = ((0, 1), (0, 10), (10, 10), (0, 800), (0, 1800))

pytestmark = pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_TRADING_SESSION_LIVE") != "1",
    reason="set MOOTDX_RUN_TRADING_SESSION_LIVE=1 during an A-share trading session",
)


def _servers() -> list[ServerEndpoint]:
    return [
        ServerEndpoint(host=host, port=port, label=label)
        for label, host, port in PREFERRED_HQ_HOSTS[:5]
    ]


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _is_weekday_trading_session() -> bool:
    now = datetime.now()
    return now.weekday() < 5 and is_trading_session(now)


@pytest.fixture(scope="module", autouse=True)
def require_trading_session() -> None:
    if _is_weekday_trading_session():
        return
    if os.getenv("MOOTDX_REQUIRE_TRADING_SESSION") == "1":
        pytest.fail(
            "scheduled trading-session matrix did not start during a weekday trading session"
        )
    pytest.skip(
        "today's populated minute/transaction matrix requires a weekday trading session"
    )


@pytest.fixture(scope="module")
def live_snapshot() -> dict[str, object]:
    client = SyncClient(servers=_servers(), max_retries=2)
    try:
        minute_rows = client.minute(SYMBOL)
        explicit_minute_rows = client.minutes(SYMBOL, _today())
        transactions = {
            f"{start}:{offset}": client.transaction(SYMBOL, start=start, offset=offset)
            for start, offset in TRANSACTION_WINDOWS
        }
    finally:
        client.close()

    return {
        "minute": minute_rows,
        "minutes": explicit_minute_rows,
        "transactions": transactions,
    }


def test_populated_today_minute_raw_matrix(live_snapshot: dict[str, object]) -> None:
    minute_rows = live_snapshot["minute"]
    explicit_rows = live_snapshot["minutes"]
    assert isinstance(minute_rows, list) and minute_rows
    assert isinstance(explicit_rows, list) and explicit_rows
    assert {"price", "vol", "volume"} <= set(minute_rows[0])
    assert len(minute_rows) <= 240
    assert len(explicit_rows) <= 240

    stable_count = max(0, min(len(minute_rows), len(explicit_rows)) - 1)
    assert minute_rows[:stable_count] == explicit_rows[:stable_count]


def test_populated_today_transaction_window_matrix(
    live_snapshot: dict[str, object],
) -> None:
    transactions = live_snapshot["transactions"]
    assert isinstance(transactions, dict)

    for start, offset in TRANSACTION_WINDOWS:
        rows = transactions[f"{start}:{offset}"]
        assert rows
        assert len(rows) <= offset
        assert {"time", "price", "vol", "num", "buyorsell", "volume"} <= set(rows[0])


class SnapshotRawClient:
    closed = False

    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot

    def minutes(self, symbol: str, date: str):
        assert symbol == SYMBOL
        assert str(date) == _today()
        return self.snapshot["minutes"]

    def transaction(self, symbol: str, start: int = 0, offset: int = 800):
        assert symbol == SYMBOL
        return self.snapshot["transactions"][f"{start}:{offset}"]

    def close(self) -> None:
        self.closed = True


def test_high_level_wrappers_match_same_response_artifact(
    live_snapshot: dict[str, object],
) -> None:
    client = PandasClient(raw_client=SnapshotRawClient(live_snapshot))
    minute = client.minute(SYMBOL)
    expected_minute = minutes_to_frame(live_snapshot["minutes"])
    transactions = {
        f"{start}:{offset}": client.transaction(SYMBOL, start=start, offset=offset)
        for start, offset in TRANSACTION_WINDOWS
    }

    pdt.assert_frame_equal(minute, expected_minute)
    for start, offset in TRANSACTION_WINDOWS:
        expected = transaction_to_frame(
            live_snapshot["transactions"][f"{start}:{offset}"]
        )
        pdt.assert_frame_equal(transactions[f"{start}:{offset}"], expected)

    write_json(
        ARTIFACTS / "trading-session-high-level-snapshot.json",
        {
            "captured_at": datetime.now().isoformat(),
            "minute": build_artifact(
                case_id="today_sh_600036",
                api="minute",
                comparator="table_exact",
                result=minute,
            ),
            "transactions": {
                key: build_artifact(
                    case_id=f"today_sh_600036_{key.replace(':', '_')}",
                    api="transaction",
                    comparator="table_exact",
                    result=value,
                )
                for key, value in transactions.items()
            },
        },
    )


def test_quotes_factory_next_populated_high_level_live_artifact() -> None:
    client = Quotes.factory(engine="next", servers=_servers(), timeout=5)
    try:
        minute = client.minute(SYMBOL)
        explicit_minute = client.minutes(SYMBOL, _today())
        transaction = client.transaction(SYMBOL, start=0, offset=1800)
    finally:
        client.close()

    assert not minute.empty
    assert not explicit_minute.empty
    assert not transaction.empty
    assert isinstance(minute.index, pd.RangeIndex)
    assert isinstance(explicit_minute.index, pd.RangeIndex)
    assert len(minute) <= 240
    assert len(explicit_minute) <= 240
    assert len(transaction) <= 1800

    write_json(
        ARTIFACTS / "trading-session-quotes-factory-next.json",
        {
            "captured_at": datetime.now().isoformat(),
            "minute": build_artifact(
                case_id="today_sh_600036",
                api="minute",
                comparator="table_exact",
                result=minute,
            ),
            "minutes": build_artifact(
                case_id="today_sh_600036",
                api="minutes",
                comparator="table_exact",
                result=explicit_minute,
            ),
            "transaction": build_artifact(
                case_id="today_sh_600036_offset1800",
                api="transaction",
                comparator="table_exact",
                result=transaction,
            ),
        },
    )


def test_async_today_minute_and_transaction_boundaries() -> None:
    async def run() -> tuple[
        list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]
    ]:
        sync_client = SyncClient(servers=_servers(), max_retries=2)
        client = AsyncClient(sync_client=sync_client)
        try:
            minute = await client.minute(SYMBOL)
            transaction_one = await client.transaction(SYMBOL, start=0, offset=1)
            transaction_max = await client.transaction(SYMBOL, start=0, offset=1800)
            return minute, transaction_one, transaction_max
        finally:
            client.close()

    minute, transaction_one, transaction_max = asyncio.run(run())

    assert minute
    assert len(transaction_one) == 1
    assert transaction_max
    assert len(transaction_max) <= 1800


@pytest.mark.network
@pytest.mark.parametrize("api", ["minutes", "transaction"])
def test_today_protocol_decode_matches_legacy_artifact(api: str) -> None:
    kwargs = (
        {"symbol": SYMBOL, "date": _today()}
        if api == "minutes"
        else {
            "symbol": SYMBOL,
            "start": 0,
            "offset": 10,
        }
    )
    spec = {
        "case_id": f"today_sh_600036_{api}",
        "api": api,
        "comparator": "table_exact",
        "client": {
            "kind": "quotes",
            "factory": {
                "market": "std",
                "timeout": 5,
                "raise_exception": True,
                "server": ["110.41.174.169", 7709],
            },
        },
        "call": {"kwargs": kwargs},
    }

    legacy, next_artifact = build_live_decode_artifact_pair(spec)

    assert legacy["status"] == "ok"
    assert legacy["result"]["records"]
    assert compare_payloads(legacy, next_artifact, "table_exact") == []
    write_json(ARTIFACTS / f"trading-session-{api}-legacy-raw.json", legacy)
    write_json(ARTIFACTS / f"trading-session-{api}-next-raw.json", next_artifact)
