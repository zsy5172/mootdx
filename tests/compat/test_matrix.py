from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from compat.common import load_json
from compat.matrix import coverage_summary
from compat.matrix import iter_cases
from compat.matrix import write_specs
from compat.registry import generated_spec_paths


def test_generated_specs_match_committed_specs(tmp_path: Path) -> None:
    generated = write_specs(tmp_path / "specs")

    assert sorted(generated) == generated_spec_paths(tmp_path / "specs")

    for case in iter_cases():
        committed = load_json(Path("compat/specs") / case.api / f"{case.case_id}.json")
        generated_payload = load_json(tmp_path / "specs" / case.api / f"{case.case_id}.json")
        assert committed == generated_payload


def test_matrix_coverage_summary_has_required_dimensions() -> None:
    summary = coverage_summary()

    assert summary["quotes"]["batch_size"] == [1, 2, 3]
    assert summary["quotes"]["duplicates"] == [False, True]
    assert summary["bars"]["frequency"] == ["5m", "daily", "monthly", "weekly"]
    assert summary["bars"]["market"] == ["bj", "sh", "sz"]
    assert summary["minutes"]["result_shape"] == ["empty", "populated"]
    assert summary["transactions"]["window"] == ["head", "mid"]
    assert summary["finance"]["market"] == ["sh", "sz"]
    assert summary["xdxr"]["market"] == ["sh", "sz"]
    assert summary["f10_categories"]["result_shape"] == ["empty", "populated"]
    assert summary["index_bars"]["frequency"] == ["5m", "daily"]
    assert summary["block"]["block_file"] == ["block.dat", "block_zs.dat"]
    assert summary["get_k_data"]["result_shape"] == ["empty"]


def test_matrix_inventory_import_does_not_require_legacy_extra() -> None:
    root = Path(__file__).resolve().parents[2]
    script = f"""
import sys
sys.path.insert(0, {str(root)!r})

class BlockTdxpy:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tdxpy" or fullname.startswith("tdxpy."):
            raise ImportError("tdxpy is blocked")
        return None

sys.meta_path.insert(0, BlockTdxpy())
from compat.registry import generated_spec_paths
assert generated_spec_paths({str(root / "compat" / "specs")!r})
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
