from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NEXT_ROOT = ROOT / "mootdx_next"


def test_next_production_tree_has_no_legacy_imports() -> None:
    violations: list[str] = []
    for path in sorted(NEXT_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "mootdx" or node.module.startswith("mootdx."):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "mootdx" or alias.name.startswith("mootdx."):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: import {alias.name}")

    assert violations == []


def test_next_imports_when_legacy_namespace_is_blocked() -> None:
    script = f"""
import importlib
import pkgutil
import sys

sys.path.insert(0, {str(ROOT)!r})

class BlockLegacy:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mootdx" or fullname.startswith("mootdx."):
            raise ImportError("legacy mootdx namespace is blocked")
        return None

sys.meta_path.insert(0, BlockLegacy())
package = importlib.import_module("mootdx_next")
for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(module.name)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
