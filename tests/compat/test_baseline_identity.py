from __future__ import annotations

import pytest

from compat.runners import verify_baseline


def test_baseline_identity_rejects_workspace_import(monkeypatch: pytest.MonkeyPatch) -> None:
    class Module:
        __file__ = "/workspace/mootdx/__init__.py"

    monkeypatch.setitem(__import__("sys").modules, "mootdx", Module())
    monkeypatch.setitem(__import__("sys").modules, "tdxpy", Module())
    monkeypatch.setattr(verify_baseline.importlib.metadata, "version", lambda name: verify_baseline.EXPECTED[name])

    with pytest.raises(RuntimeError, match="workspace package"):
        verify_baseline.verify()
