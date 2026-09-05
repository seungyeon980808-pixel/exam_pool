"""Fail-closed stage controller for the PDF -> HWP -> endnote workflow."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class PipelineStage(StrEnum):
    INPUT_INVENTORY = "input_inventory"
    PROBLEM_EDITABLE_QA = "problem_editable_qa"
    SOLUTION_EDITABLE_QA = "solution_editable_qa"
    ITEM_MAPPING = "item_mapping"
    NATIVE_ENDNOTE_BUILD = "native_endnote_build"
    NATIVE_ENDNOTE_QA = "native_endnote_qa"


STAGE_ORDER = tuple(PipelineStage)
_PASS_STATUSES = {
    PipelineStage.INPUT_INVENTORY: frozenset({"PASS"}),
    PipelineStage.PROBLEM_EDITABLE_QA: frozenset({"STRICT_PASS", "PRACTICAL_PASS_WITH_EXCEPTIONS"}),
    PipelineStage.SOLUTION_EDITABLE_QA: frozenset({"STRICT_PASS", "PRACTICAL_PASS_WITH_EXCEPTIONS"}),
    PipelineStage.ITEM_MAPPING: frozenset({"PASS"}),
    PipelineStage.NATIVE_ENDNOTE_BUILD: frozenset({"BUILT_REQUIRES_RUNTIME_QA"}),
    PipelineStage.NATIVE_ENDNOTE_QA: frozenset({"PASS"}),
}


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: PipelineStage
    status: str
    report_path: Path | None = None
    detail: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, base_dir: Path | None = None) -> "StageResult":
        path = Path(str(value["report_path"])) if value.get("report_path") else None
        if path is not None and not path.is_absolute():
            path = ((base_dir or Path.cwd()) / path).resolve()
        return cls(PipelineStage(str(value["stage"])), str(value["status"]), path, str(value.get("detail", "")))

    @property
    def passed(self) -> bool:
        return self.status in _PASS_STATUSES[self.stage]


@dataclass(frozen=True, slots=True)
class PipelineGateReport:
    stages: tuple[StageResult, ...]
    next_stage: PipelineStage | None
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors and self.next_stage is None and len(self.stages) == len(STAGE_ORDER)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "PASS" if self.passed else "BLOCKED",
            "next_stage": self.next_stage.value if self.next_stage else None,
            "errors": list(self.errors),
            "stages": [
                {
                    "stage": result.stage.value,
                    "status": result.status,
                    "passed": result.passed,
                    "report_path": str(result.report_path) if result.report_path else None,
                    "detail": result.detail,
                }
                for result in self.stages
            ],
        }


def load_stage_report(stage: PipelineStage, path: Path) -> StageResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return StageResult(stage, str(payload.get("status", payload.get("overall_status", "FAIL"))), path)


def evaluate_pipeline_stages(results: Sequence[StageResult]) -> PipelineGateReport:
    errors: list[str] = []
    by_stage: dict[PipelineStage, StageResult] = {}
    for result in results:
        if result.stage in by_stage:
            errors.append(f"duplicate stage result: {result.stage.value}")
        by_stage[result.stage] = result
    next_stage: PipelineStage | None = None
    ordered: list[StageResult] = []
    blocked = False
    for stage in STAGE_ORDER:
        result = by_stage.get(stage)
        if result is None:
            next_stage = stage
            break
        ordered.append(result)
        if blocked:
            errors.append(f"stage executed after an earlier failure: {stage.value}")
        if not result.passed:
            errors.append(f"{stage.value} did not pass: {result.status}")
            blocked = True
            next_stage = stage
            break
        if result.report_path is not None and not result.report_path.is_file():
            errors.append(f"missing report file: {result.report_path}")
            blocked = True
            next_stage = stage
            break
    return PipelineGateReport(tuple(ordered), next_stage, tuple(errors))


def require_previous_stages(
    results: Sequence[StageResult],
    requested_stage: PipelineStage,
) -> None:
    required = STAGE_ORDER[: STAGE_ORDER.index(requested_stage)]
    by_stage = {result.stage: result for result in results}
    failures = [
        f"{stage.value}={by_stage.get(stage).status if by_stage.get(stage) else 'MISSING'}"
        for stage in required
        if stage not in by_stage or not by_stage[stage].passed
    ]
    if failures:
        raise RuntimeError(
            f"cannot start {requested_stage.value}; previous QA gate failed: " + ", ".join(failures)
        )

