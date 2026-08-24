from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.pdf_hwp_roundtrip_harness as subject
from app.pdf_hwp_pipeline_models import LayoutStyle
from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_models import ArtifactHash, SourceKind, SourceProfile, WorkflowStage
from app.pdf_hwp_roundtrip_reports import ReportPaths, write_reports
from app.pdf_hwp_roundtrip_runner import RunFailure, RunStatus, SourceRunResult
from app.pdf_hwp_roundtrip_unit_store import (
    FailureCode,
    ItemFailure,
    PreparationPayload,
    write_prepared_units,
)
from tools.pdf_hwp_roundtrip_harness import (
    HarnessResult,
    _is_expected_regression_failure,
    approved_exit_code,
)
from tools.pdf_hwp_roundtrip_harness_contract import Candidate, SourceGroup
from tools.pdf_hwp_roundtrip_harness_support import ReportSource, finalize_reports


def _failed(path: Path, code: str = "no_prepared_units") -> SourceRunResult:
    digest = ArtifactHash(artifact_hash(path))
    failure = RunFailure(digest, path, WorkflowStage.EXTRACT, code, "1:crop_clipping")
    return SourceRunResult(
        path, digest, SourceKind.KICE, RunStatus.FAILED,
        (WorkflowStage.ROUTE,), (), (failure,),
    )


def _candidate(path: Path, claims: tuple[str, ...]) -> Candidate:
    return Candidate(
        "e2_2024_09", SourceGroup.KICE, path, artifact_hash(path), (1,),
        "생명과학Ⅱ", SourceProfile.KICE_STRUCTURAL, claims,
    )


def _harness_result(tmp_path: Path, failed: SourceRunResult, expected: bool) -> HarnessResult:
    reports = ReportPaths(
        tmp_path / "summary.json", tmp_path / "failures.json", tmp_path / "report.md",
    )
    return HarnessResult(
        reports, tmp_path / "contact.png", (failed,),
        (failed,) if expected else (), () if expected else (failed,),
    )


def test_approved_exit_accepts_real_persisted_pinned_regression(tmp_path: Path) -> None:
    source = tmp_path / "e2_2024_09.pdf"
    source.write_bytes(b"pinned-source")
    output = tmp_path / "output"
    write_prepared_units(
        output / "prepared-units.json",
        PreparationPayload(
            source, artifact_hash(source), LayoutStyle.SUNEUNG, (),
            (ItemFailure(1, FailureCode.CROP_CLIPPING, "pinned", "1" * 64),),
        ),
    )
    failed = _failed(source)

    matched = _is_expected_regression_failure(
        _candidate(source, ("e2_2024_09_q1_clipping:1:crop_clipping",)),
        output,
        failed,
    )

    assert matched is True
    assert approved_exit_code(_harness_result(tmp_path, failed, matched)) == 0


def test_approved_exit_rejects_fake_label_on_unexpected_failure(tmp_path: Path) -> None:
    source = tmp_path / "e2_2024_09.pdf"
    source.write_bytes(b"pinned-source")
    failed = _failed(source, "source_detection_failed")

    matched = _is_expected_regression_failure(
        _candidate(source, ("e2_2024_09_q1_clipping:1:crop_clipping",)),
        tmp_path / "missing-output",
        failed,
    )

    assert matched is False
    assert approved_exit_code(_harness_result(tmp_path, failed, matched)) == 1


def test_approved_exit_rejects_additional_preparation_failure_from_summary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "e2_2024_09.pdf"
    source.write_bytes(b"pinned-source")
    failed = _failed(source)
    result = _harness_result(tmp_path, failed, expected=True)
    result.reports.summary.write_text(json.dumps({
        "unexpected_preparation_verification_failure_count": 1,
    }), encoding="utf-8")

    assert approved_exit_code(result) == 1


@pytest.mark.parametrize(("include_additional", "unexpected"), [(False, 0), (True, 1)])
def test_report_counts_partial_pinned_failures_without_hiding_additional_item(
    include_additional: bool, unexpected: int, tmp_path: Path,
) -> None:
    source = tmp_path / "ebs.pdf"
    source.write_bytes(b"ebs-source")
    output = tmp_path / "output"
    pinned = (
        ItemFailure(35, FailureCode.CROP_CONTAMINATION, "pinned-35", "1" * 64),
        ItemFailure(37, FailureCode.CROP_CONTAMINATION, "pinned-37", "2" * 64),
    )
    additional = (
        (ItemFailure(168, FailureCode.CROP_CONTAMINATION, "additional", "3" * 64),)
        if include_additional else ()
    )
    failures = (*pinned, *additional)
    write_prepared_units(output / "prepared-units.json", PreparationPayload(
        source, artifact_hash(source), LayoutStyle.SUNEUNG, (), failures,
        profile=SourceProfile.EBS_EDITABLE_REFLOW,
    ))
    failed = _failed(source)
    reports = write_reports((failed,), tmp_path)
    claims = (
        "ebs_q35_crop:35:crop_contamination",
        "ebs_q37_crop:37:crop_contamination",
    )

    finalize_reports(
        reports,
        (ReportSource(
            "ebs", output, failed, SourceProfile.EBS_EDITABLE_REFLOW, claims,
        ),),
        tmp_path,
        {},
        claims,
    )
    summary = json.loads(reports.summary.read_text(encoding="utf-8"))

    assert summary["preparation_verification_failure_count"] == 2 + unexpected
    assert summary["expected_preparation_verification_failure_count"] == 2
    assert summary["unexpected_preparation_verification_failure_count"] == unexpected
    guarded = HarnessResult(
        reports, tmp_path / "contact-sheet.png", (failed,), (failed,), (),
    )
    assert approved_exit_code(guarded) == int(include_additional)


@pytest.mark.parametrize(("expected", "exit_code"), [(True, 0), (False, 1)])
def test_shipped_cli_exit_and_summary_follow_failure_classification(
    expected: bool,
    exit_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    failed = _failed(source)
    result = _harness_result(tmp_path, failed, expected)
    monkeypatch.setattr(subject, "run_harness", lambda options: result)

    observed = subject.main(("--run-root", str(tmp_path)))

    output = capsys.readouterr().out.splitlines()
    assert observed == exit_code
    assert output[0].endswith(
        f"expected_failed={int(expected)} unexpected_failed={int(not expected)}",
    )
    assert output[1:] == [
        f"summary={(tmp_path / 'summary.json').resolve()}",
        f"failures={(tmp_path / 'failures.json').resolve()}",
        f"report={(tmp_path / 'report.md').resolve()}",
        f"contact_sheet={(tmp_path / 'contact.png').resolve()}",
    ]
