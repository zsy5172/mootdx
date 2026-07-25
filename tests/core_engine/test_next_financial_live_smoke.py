from __future__ import annotations

import os

import pytest

from mootdx.affair import Affair
from mootdx_next import FinancialFileClient
from mootdx_next.affair import Affair as NextAffair


def test_legacy_affair_is_the_next_https_facade() -> None:
    assert Affair is NextAffair


@pytest.mark.skipif(
    os.getenv("MOOTDX_RUN_FINANCIAL_LIVE") != "1",
    reason="set MOOTDX_RUN_FINANCIAL_LIVE=1 to query the official TDX catalog",
)
def test_official_financial_catalog_live() -> None:
    with FinancialFileClient() as client:
        files = client.files(refresh=True)

    assert files
    assert all({"filename", "hash", "filesize"} <= set(item) for item in files)
