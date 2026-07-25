from __future__ import annotations

from pathlib import Path

import pytest

from compat.common import load_json
from compat.comparators import compare_payloads
from compat.reader_matrix import capture_reader_case
from compat.reader_matrix import READER_CASES

ROOT = Path(__file__).resolve().parents[2]
BASELINES = ROOT / "compat" / "reader_baselines" / "v1"
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.mark.parametrize("case", READER_CASES, ids=lambda case: case.case_id)
def test_next_reader_matches_python311_mootdx0117_baseline(case) -> None:
    expected = load_json(BASELINES / f"{case.case_id}.json")
    actual = capture_reader_case(case, runtime="next", tdxdir=FIXTURES)

    assert compare_payloads(expected, actual, "scalar_exact") == []
