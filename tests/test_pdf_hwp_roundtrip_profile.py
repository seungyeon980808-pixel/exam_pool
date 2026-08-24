from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.pdf_hwp_pipeline_models import ConversionUnit, LayoutStyle
from app.pdf_hwp_roundtrip_models import ArtifactHash, SourceKind, SourceProfile
from app.pdf_hwp_roundtrip_profile import (
    ImageOwnershipResult,
    IssueSeverity,
    ObservedProfileIssue,
    ProfileVerificationRequest,
    VisualMetric,
    classify_profile,
    write_profile_verification,
)
from app.pdf_hwp_roundtrip_runner import RunStatus, SourceRunResult
from app.pdf_hwp_roundtrip_structure import (
    AssetRole,
    PreparedAssetRef,
    parse_prepared_structure,
)
from app.pdf_hwp_roundtrip_unit_store import FailureCode, ItemFailure, PreparedUnitRecord
from tools.pdf_hwp_roundtrip_harness_support import ReportSource
from tools.pdf_hwp_roundtrip_profile_reports import write_profile_reports


def _record(item_number: int = 7, *, wrong_owner: bool = False) -> PreparedUnitRecord:
    unit = ConversionUnit(
        item_number,
        f"\\수능정답1대사진5선지\\\n{item_number}\n완전한 문두이다.\n-\n발문은?\n①\n②\n③\n④\n⑤",
    )
    structure = parse_prepared_structure(
        unit, 3, (1.0, 2.0, 30.0, 40.0), LayoutStyle.SUNEUNG,
    )
    if wrong_owner:
        structure = structure.model_copy(update={"asset_refs": (PreparedAssetRef(
            role=AssetRole.MATERIAL, asset_path=Path("wrong.png"), slot_name="사진1",
            owner_item_number=item_number + 1, source_page=3, asset_hash="a" * 64,
            order=1,
        ),)})
    return PreparedUnitRecord(unit, "b" * 64, structure)


def _request(
    profile: SourceProfile,
    *,
    generated_count: int = 1,
    alignment: tuple[ObservedProfileIssue, ...] = (),
    wrong_owner: bool = False,
) -> ProfileVerificationRequest:
    records = (_record(wrong_owner=wrong_owner),)
    return ProfileVerificationRequest(
        profile=profile,
        records=records,
        preparation_failures=(),
        generated_count=generated_count,
        editability_issues=(),
        pdf_issues=(),
        semantic_issues=(),
        alignment_issues=alignment,
        visual_metrics=(VisualMetric(7, 0.12, 0.08),),
        image_ownership=ImageOwnershipResult.from_records(records),
    )


def test_visual_mismatch_and_mae_are_diagnostic_only() -> None:
    # Given
    request = _request(
        SourceProfile.EBS_EDITABLE_REFLOW,
        alignment=(ObservedProfileIssue("visual_mismatch", 7, "threshold exceeded"),),
    )

    # When
    record = classify_profile(request)

    # Then
    assert record.blocking_issues == ()
    assert {issue.code for issue in record.diagnostics} == {"visual_mismatch", "visual_metric"}
    assert all(issue.severity is IssueSeverity.DIAGNOSTIC for issue in record.diagnostics)
    assert record.prepared_count == record.generated_count == 1
    assert record.structural_coverage.complete_count == 1


@pytest.mark.parametrize("profile", tuple(SourceProfile))
def test_count_mismatch_and_wrong_ownership_block_both_profiles(
    profile: SourceProfile,
) -> None:
    # Given / When
    record = classify_profile(_request(profile, generated_count=0, wrong_owner=True))

    # Then
    assert {issue.code for issue in record.blocking_issues} == {
        "generated_item_count_mismatch", "wrong_image_ownership",
    }
    assert record.image_ownership.passed is False


def test_kice_requires_full_typed_coverage_in_addition_to_structural_failure() -> None:
    failure = ItemFailure(8, FailureCode.MISSING_STEM, "fragment stem", "c" * 64)
    ebs = classify_profile(replace(
        _request(SourceProfile.EBS_EDITABLE_REFLOW), preparation_failures=(failure,),
    ))
    kice = classify_profile(replace(
        _request(SourceProfile.KICE_STRUCTURAL), preparation_failures=(failure,),
    ))

    assert {issue.code for issue in ebs.blocking_issues} == {"missing_stem"}
    assert {issue.code for issue in kice.blocking_issues} == {
        "missing_stem", "typed_structure_coverage_incomplete",
    }
    assert kice.structural_coverage.complete_count == 1
    assert kice.structural_coverage.total_count == 2


def test_profile_reports_keep_diagnostics_out_of_failures(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "source"
    diagnostic = classify_profile(_request(
        SourceProfile.EBS_EDITABLE_REFLOW,
        alignment=(ObservedProfileIssue("visual_mismatch", 7, "different font"),),
    ))
    write_profile_verification(output / "profile-verification.json", diagnostic)
    result = SourceRunResult(
        tmp_path / "source.pdf", ArtifactHash("a" * 64), SourceKind.EBS,
        RunStatus.SUCCEEDED, (),
    )
    source = ReportSource("ebs", output, result, SourceProfile.EBS_EDITABLE_REFLOW)

    # When
    paths = write_profile_reports((source,), tmp_path)

    # Then
    summary = json.loads(paths.summary.read_text(encoding="utf-8"))
    failures = json.loads(paths.failures.read_text(encoding="utf-8"))
    assert summary["sources"][0]["profile"] == "ebs_editable_reflow"
    assert summary["sources"][0]["diagnostic_count"] == 2
    assert failures == {"schema_version": 1, "failures": []}
