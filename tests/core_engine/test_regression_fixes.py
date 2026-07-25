from __future__ import annotations

import pandas as pd
import pytest

import mootdx.config as config_module
import mootdx.quotes as quotes_module
import mootdx.server as server_module
from mootdx.consts import HQ_HOSTS
from mootdx.financial.base import BaseFinancial
from mootdx.quotes import _resolve_bestip_server
from mootdx.quotes import check_empty
from mootdx_next.candidates import ServerCandidate


def test_bestip_refreshes_process_registry_without_writing_config(tmp_path, monkeypatch) -> None:
    output = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONF", str(output))
    monkeypatch.setattr(
        server_module,
        "refresh_hq_candidates",
        lambda: (
            ServerCandidate(
                host="1.1.1.1",
                port=7709,
                label="fast",
                latency_ms=10.0,
            ),
        ),
    )

    server_module.bestip(sync=True, limit=1, console=False)

    assert output.exists() is False
    assert server_module.results["HQ"] == [
        {
            "addr": "1.1.1.1",
            "port": 7709,
            "time": 10.0,
            "site": "fast",
        }
    ]


def test_connect2_invalid_index_does_not_raise() -> None:
    proxy = {"addr": "127.0.0.1", "port": 7709, "site": "local", "time": 0}

    result = server_module.connect2(proxy, index="GP")

    assert result["time"] is None


def test_hq_hosts_are_unique_and_include_verified_supplemental_nodes() -> None:
    addresses = [(host, port) for _, host, port in HQ_HOSTS]

    assert len(HQ_HOSTS) == 30
    assert len(addresses) == len(set(addresses))
    assert {
        ("182.140.139.191", 7709),
        ("119.6.200.40", 7709),
        ("218.200.222.134", 7709),
        ("182.150.28.166", 7709),
    } <= set(addresses)


@pytest.mark.parametrize("sync", [True, False])
def test_server_orders_probe_results_and_applies_limit(monkeypatch, sync) -> None:
    candidates = [
        {"addr": "1.1.1.1", "port": 7709, "time": 0, "site": "slow"},
        {"addr": "2.2.2.2", "port": 7709, "time": 0, "site": "fast"},
        {"addr": "3.3.3.3", "port": 7709, "time": 0, "site": "medium"},
    ]
    latencies = {"1.1.1.1": 30.0, "2.2.2.2": 10.0, "3.3.3.3": 20.0}

    monkeypatch.setitem(server_module.hosts, "HQ", candidates)
    monkeypatch.setattr(
        server_module,
        "connect2",
        lambda proxy, index="HQ": {**proxy, "time": latencies[proxy["addr"]]},
    )

    assert server_module.server(index="HQ", limit=2, console=False, sync=sync) == [
        ("2.2.2.2", 7709),
        ("3.3.3.3", 7709),
    ]


def test_check_empty_is_safe_before_any_instance_exists(monkeypatch) -> None:
    monkeypatch.setattr(quotes_module, "instance", None)

    assert check_empty(pd.DataFrame()) is True


def test_resolve_bestip_server_falls_back_when_config_value_is_empty() -> None:
    original = config_module.clone()
    try:
        config_module.set("BESTIP", {"HQ": "", "EX": "", "GP": ""})
        assert _resolve_bestip_server("HQ", ("8.8.8.8", 7709)) == ("8.8.8.8", 7709)
    finally:
        config_module.update(original)


def test_base_financial_init_tolerates_missing_gp_config(monkeypatch) -> None:
    original = config_module.clone()
    try:
        monkeypatch.setattr(config_module, "setup", lambda: True)
        config_module.update({"SERVER": {"HQ": [], "EX": [], "GP": []}, "BESTIP": {"HQ": None, "EX": None, "GP": None}})
        base = BaseFinancial()
        assert base.bestip is None
    finally:
        config_module.update(original)
