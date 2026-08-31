from __future__ import annotations

import pytest

from app.pdf_hwp_pipeline_gate import (
    PipelineStage,
    StageResult,
    evaluate_pipeline_stages,
    require_previous_stages,
)


def test_failed_editable_gate_blocks_mapping_and_endnotes() -> None:
    results = (
        StageResult(PipelineStage.INPUT_INVENTORY, "PASS"),
        StageResult(PipelineStage.PROBLEM_EDITABLE_QA, "FAIL"),
    )
    report = evaluate_pipeline_stages(results)
    assert not report.passed
    assert report.next_stage is PipelineStage.PROBLEM_EDITABLE_QA
    with pytest.raises(RuntimeError, match="previous QA gate failed"):
        require_previous_stages(results, PipelineStage.NATIVE_ENDNOTE_BUILD)


def test_complete_stage_chain_passes() -> None:
    statuses = {
        PipelineStage.INPUT_INVENTORY: "PASS",
        PipelineStage.PROBLEM_EDITABLE_QA: "STRICT_PASS",
        PipelineStage.SOLUTION_EDITABLE_QA: "PRACTICAL_PASS_WITH_EXCEPTIONS",
        PipelineStage.ITEM_MAPPING: "PASS",
        PipelineStage.NATIVE_ENDNOTE_BUILD: "BUILT_REQUIRES_RUNTIME_QA",
        PipelineStage.NATIVE_ENDNOTE_QA: "PASS",
    }
    report = evaluate_pipeline_stages(tuple(StageResult(stage, statuses[stage]) for stage in PipelineStage))
    assert report.passed
    assert report.next_stage is None

