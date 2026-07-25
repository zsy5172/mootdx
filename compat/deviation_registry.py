from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from compat.common import load_json


DEFAULT_DEVIATIONS_PATH = Path(__file__).with_name("deviations") / "v1.json"
REQUIRED_FIELDS = {
    "id",
    "apis",
    "case_ids",
    "legacy_behavior",
    "next_behavior",
    "rationale",
    "evidence",
    "regression_tests",
    "allowed_paths",
    "status",
}


@dataclass(frozen=True, slots=True)
class Deviation:
    id: str
    apis: tuple[str, ...]
    case_ids: tuple[str, ...]
    legacy_behavior: str
    next_behavior: str
    rationale: str
    evidence: tuple[str, ...]
    regression_tests: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    status: str

    def allows(self, api: str, case_id: str, diff: str) -> bool:
        if self.status != "accepted":
            return False
        if api not in self.apis or case_id not in self.case_ids:
            return False
        path = diff.split(":", 1)[0]
        return any(path == allowed or path.startswith(f"{allowed}.") or path.startswith(f"{allowed}[") for allowed in self.allowed_paths)


class DeviationRegistry:
    def __init__(self, deviations: tuple[Deviation, ...], *, baseline: dict[str, str], version: int) -> None:
        self.deviations = deviations
        self.baseline = dict(baseline)
        self.version = int(version)
        self._by_id = {item.id: item for item in deviations}
        if len(self._by_id) != len(deviations):
            raise ValueError("duplicate deviation id")

    @classmethod
    def load(cls, path: str | Path = DEFAULT_DEVIATIONS_PATH) -> "DeviationRegistry":
        payload = load_json(path)
        _validate_payload(payload)
        deviations = tuple(
            Deviation(
                id=item["id"],
                apis=tuple(item["apis"]),
                case_ids=tuple(item["case_ids"]),
                legacy_behavior=item["legacy_behavior"],
                next_behavior=item["next_behavior"],
                rationale=item["rationale"],
                evidence=tuple(item["evidence"]),
                regression_tests=tuple(item["regression_tests"]),
                allowed_paths=tuple(item["allowed_paths"]),
                status=item["status"],
            )
            for item in payload["deviations"]
        )
        return cls(deviations, baseline=payload["baseline"], version=payload["version"])

    def require(self, deviation_id: str) -> Deviation:
        try:
            return self._by_id[deviation_id]
        except KeyError as exc:
            raise ValueError(f"unknown compatibility deviation: {deviation_id}") from exc

    def filter_diffs(
        self,
        diffs: list[str],
        *,
        deviation_id: str | None,
        api: str,
        case_id: str,
    ) -> list[str]:
        if not diffs or deviation_id is None:
            return list(diffs)

        deviation = self.require(deviation_id)
        return [diff for diff in diffs if not deviation.allows(api, case_id, diff)]


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("version") != 1:
        raise ValueError("unsupported compatibility deviation version")

    baseline = payload.get("baseline")
    if not isinstance(baseline, dict) or baseline.get("python") != "3.11":
        raise ValueError("deviation baseline must pin Python 3.11")
    if baseline.get("mootdx") != "0.11.7" or baseline.get("tdxpy") != "0.2.7":
        raise ValueError("deviation baseline must pin mootdx 0.11.7 and tdxpy 0.2.7")

    entries = payload.get("deviations")
    if not isinstance(entries, list):
        raise ValueError("deviations must be a list")

    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("each deviation must be an object")
        missing = REQUIRED_FIELDS - set(item)
        if missing:
            raise ValueError(f"deviation is missing fields: {', '.join(sorted(missing))}")
        if item["status"] not in {"accepted", "retired"}:
            raise ValueError(f"unsupported deviation status: {item['status']}")
        for field in ("apis", "case_ids", "evidence", "regression_tests", "allowed_paths"):
            if not isinstance(item[field], list) or not all(isinstance(value, str) and value for value in item[field]):
                raise ValueError(f"deviation field {field} must be a non-empty string list")
        for field in ("id", "legacy_behavior", "next_behavior", "rationale"):
            if not isinstance(item[field], str) or not item[field]:
                raise ValueError(f"deviation field {field} must be a non-empty string")
