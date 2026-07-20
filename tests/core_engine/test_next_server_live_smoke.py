from __future__ import annotations

import pytest

import mootdx.server as server_module
from mootdx.consts import EX_HOSTS
from mootdx.exceptions import MootdxValidationException
from mootdx.server import server
from tests.core_engine.support import PREFERRED_HQ_HOSTS


def _host_entries(items):
    return [{"addr": host, "port": port, "time": 0, "site": name} for name, host, port in items]


def test_hq_server_probe_returns_reachable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(server_module.hosts, "HQ", _host_entries(PREFERRED_HQ_HOSTS[:5]))
    result = server(index="HQ", limit=1, sync=True)
    assert result


def test_gp_server_probe_is_explicitly_unsupported() -> None:
    with pytest.raises(MootdxValidationException, match="GP 财务下载线路已经废弃且不再支持"):
        server(index="GP", limit=1, sync=True)


def test_ex_server_probe_returns_reachable_host_or_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(server_module.hosts, "EX", _host_entries(EX_HOSTS))
    result = server(index="EX", limit=1, sync=True)
    if not result:
        pytest.skip("no reachable EX probe host in current environment")
    assert result
