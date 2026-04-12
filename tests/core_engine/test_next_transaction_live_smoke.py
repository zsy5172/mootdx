from __future__ import annotations

from pathlib import Path

import pytest

from compat.comparators import compare_payloads
from compat.registry import build_live_decode_artifact_pair
from compat.registry import load_spec
from mootdx_next import ServerEndpoint
from mootdx_next import SyncClient
from mootdx_next.session import is_trading_session
from tests.core_engine.support import PREFERRED_HQ_HOSTS

ROOT = Path(__file__).resolve().parents[2]


def _preferred_servers() -> list[ServerEndpoint]:
    return [ServerEndpoint(host=host, port=port, label=label) for label, host, port in PREFERRED_HQ_HOSTS]


@pytest.mark.network
def test_next_transactions_live_smoke() -> None:
    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.transactions(symbol="600036", date="20170209", offset=10)
    finally:
        client.connection_pool.close_all()

    assert rows
    assert len(rows) == 10
    assert {"time", "price", "vol", "buyorsell", "volume"} <= set(rows[0])


@pytest.mark.network
def test_transaction_live_decode_parity() -> None:
    if not is_trading_session():
        pytest.skip("transaction live parity requires a trading session")

    spec = load_spec(ROOT / "compat" / "specs" / "transaction" / "live_sh_600036_last10.json")
    legacy, next_artifact = build_live_decode_artifact_pair(spec)

    diffs = compare_payloads(legacy, next_artifact, spec["comparator"])
    assert diffs == []


@pytest.mark.network
def test_next_transaction_live_smoke() -> None:
    if not is_trading_session():
        pytest.skip("transaction live smoke requires a trading session")

    client = SyncClient(servers=_preferred_servers())
    try:
        rows = client.transaction(symbol="600036", offset=10)
    finally:
        client.connection_pool.close_all()

    assert rows
    assert len(rows) == 10
    assert {"time", "price", "vol", "num", "buyorsell", "volume"} <= set(rows[0])
