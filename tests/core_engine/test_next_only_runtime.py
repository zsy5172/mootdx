from __future__ import annotations

import pytest

import mootdx
from mootdx.affair import Affair
from mootdx.exceptions import MootdxValidationException
from mootdx.quotes import NextStdQuotes
from mootdx.quotes import Quotes
from mootdx.server import server
from mootdx_next.affair import Affair as NextAffair


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


def test_ext_market_uses_decoupled_next_runtime() -> None:
    client = Quotes.factory(market="ext")
    try:
        assert client.__class__.__name__ == "NextExtQuotes"
        assert client.client.__class__.__module__ == "mootdx_next.api.ex_clients"
    finally:
        client.close()


def test_gp_socket_probe_is_unsupported_but_affair_routes_to_next_https() -> None:
    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        server(index="GP")

    assert Affair is NextAffair
