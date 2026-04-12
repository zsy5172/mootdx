from __future__ import annotations

from importlib.util import find_spec

import pytest

import mootdx
from mootdx.exceptions import MootdxModuleNotFoundError
from mootdx.quotes import NextStdQuotes
from mootdx.quotes import Quotes


class DummyNextClient:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_package_import_and_default_std_engine_work_without_legacy_extra() -> None:
    assert mootdx.__version__

    client = Quotes.factory(
        market="std",
        server=("127.0.0.1", 7709),
        engine_client=DummyNextClient(),
    )

    assert isinstance(client, NextStdQuotes)


def test_ext_market_without_legacy_extra_raises_clear_error() -> None:
    if find_spec("tdxpy") is not None:
        pytest.skip("legacy extra is installed in this environment")

    with pytest.raises(MootdxModuleNotFoundError, match=r"mootdx\[legacy\]"):
        Quotes.factory(market="ext")
