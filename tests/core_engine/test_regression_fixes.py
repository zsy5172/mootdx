from __future__ import annotations

import json

import pandas as pd
import pytest

import mootdx.config as config_module
import mootdx.quotes as quotes_module
import mootdx.server as server_module
from mootdx.financial.base import BaseFinancial
from mootdx.quotes import _resolve_bestip_server
from mootdx.quotes import check_empty


def test_bestip_writes_when_config_conf_is_str(tmp_path, monkeypatch) -> None:
    output = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONF", str(output))
    monkeypatch.setattr(
        server_module,
        "server",
        lambda index=None, limit=5, console=False, sync=False: [("1.1.1.1", 7709)] if index == "HQ" else [("2.2.2.2", 7727)],
    )

    server_module.bestip(sync=True, limit=1, console=False)

    assert output.exists()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["BESTIP"]["HQ"] == ["1.1.1.1", 7709]
    assert saved["BESTIP"]["EX"] == ["2.2.2.2", 7727]


def test_connect2_invalid_index_does_not_raise() -> None:
    proxy = {"addr": "127.0.0.1", "port": 7709, "site": "local", "time": 0}

    result = server_module.connect2(proxy, index="GP")

    assert result["time"] is None


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
