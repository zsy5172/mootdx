from __future__ import annotations

from importlib import import_module

from mootdx.exceptions import MootdxModuleNotFoundError

LEGACY_EXTRA = "mootdx[legacy]"


def missing_legacy_dependency(feature: str) -> MootdxModuleNotFoundError:
    return MootdxModuleNotFoundError(f"{feature} 依赖 tdxpy, 请先安装 `{LEGACY_EXTRA}`")


def import_legacy_module(module_name: str, feature: str):
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] == "tdxpy":
            raise missing_legacy_dependency(feature) from exc
        raise


def import_legacy_attr(module_name: str, attr_name: str, feature: str):
    module = import_legacy_module(module_name, feature)
    return getattr(module, attr_name)
