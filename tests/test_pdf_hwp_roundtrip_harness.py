from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

import tools.pdf_hwp_roundtrip_harness as subject
from app.pdf_hwp_roundtrip_backend import BackendSourcePlan
from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_models import SourceRoute, WorkflowStage
from app.pdf_hwp_roundtrip_manifest import ManifestVerification, SourceCheck
from app.pdf_hwp_roundtrip_runner import (
    BackendStageError,
    ExtractOutcome,
    HwpOutcome,
    PdfOutcome,
    RouteOutcome,
    SourceInput,
)
from app.pdf_hwp_roundtrip_router import route_source
from tools.pdf_hwp_roundtrip_harness import HarnessOptions, SourceGroup, run_harness


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "pdf_hwp_roundtrip_approved_first_run.json"


def _verification(manifest, *, bad_items: frozenset[str] = frozenset(), bad_hashes: frozenset[str] = frozenset()):
    entries = [
        (paper.paper_id, paper.path, True) for paper in manifest.kice_papers
    ]
    entries.append((manifest.ebs_source.source_id, manifest.ebs_source.path, True))
    entries.extend(
        (case.source_id, case.path, False)
        for case in manifest.fixed_regressions if case.path is not None
    )
    entries.append((manifest.raster_fixture.source_id, manifest.raster_fixture.path, False))
    entries.append((
        f"{manifest.raster_fixture.source_id}:companion",
        manifest.raster_fixture.companion_pdf_path,
        False,
    ))
    return ManifestVerification(tuple(
        SourceCheck(source_id, path, True, source_id not in bad_hashes,
                    True if has_expectations else None,
                    source_id not in bad_items if has_expectations else None)
        for source_id, path, has_expectations in entries
    ))


@pytest.fixture(autouse=True)
def _fast_verified_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    def verify(manifest, *, check_expectations: bool = False):
        assert check_expectations is True
        return _verification(manifest)

    monkeypatch.setattr(
        subject, "verify_manifest_sources", verify,
    )


class _Backend:
    calls: list[tuple[str, str]] = []

    def __init__(
        self,
        plans: tuple[BackendSourcePlan, ...],
        failures: tuple[tuple[str, WorkflowStage], ...] = (),
    ) -> None:
        self.plans = plans
        self.failures = failures

    def _plan(self, source: SourceInput) -> BackendSourcePlan:
        return next(plan for plan in self.plans if plan.original_path.resolve() == source.path.resolve())

    def _record(self, stage: WorkflowStage, source: SourceInput) -> BackendSourcePlan:
        plan = self._plan(source)
        source_id = plan.output_dir.name.split("-", 1)[0]
        self.calls.append((stage.value, source_id))
        if (source_id, stage) in self.failures:
            raise BackendStageError(stage, f"{stage.value}_failed", source_id)
        return plan

    def route(self, source: SourceInput) -> RouteOutcome:
        self._record(WorkflowStage.ROUTE, source)
        return RouteOutcome(route_source(source.facts))

    def extract(self, source: SourceInput, route: SourceRoute) -> ExtractOutcome:
        plan = self._record(WorkflowStage.EXTRACT, source)
        target = plan.output_dir / "prepared-units.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("prepared", encoding="utf-8")
        return ExtractOutcome(target)

    def typeset(self, source: SourceInput, route: SourceRoute) -> HwpOutcome:
        plan = self._record(WorkflowStage.HWP, source)
        target = plan.output_dir / "converted.hwp"
        target.write_bytes(b"hwp")
        return HwpOutcome(target)

    def verify(self, source: SourceInput, route: SourceRoute) -> PdfOutcome:
        plan = self._plan(source)
        source_id = plan.output_dir.name.split("-", 1)[0]
        if (source_id, WorkflowStage.PDF) in self.failures:
            evidence = plan.output_dir / "verification-evidence" / "item-failures.png"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 20), (255, 0, 0)).save(evidence)
        plan = self._record(WorkflowStage.PDF, source)
        target = plan.output_dir / "verified.pdf"
        target.write_bytes(b"pdf")
        return PdfOutcome(target)


def test_harness_resumes_filtered_source_and_keeps_deterministic_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Backend.calls.clear()
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans))
    first_options = HarnessOptions(
        MANIFEST, tmp_path, 2, False, (SourceGroup.KICE,), ("p1_2019_11",), 1,
    )

    first = run_harness(first_options)
    resumed = run_harness(first_options.with_stop(None))
    reports_before = tuple(
        path.read_bytes()
        for path in (resumed.reports.summary, resumed.reports.failures, resumed.reports.markdown)
    )
    contact_before = resumed.contact_sheet.read_bytes()
    repeated = run_harness(first_options.with_stop(None))

    assert first.results[0].completed_stages == (WorkflowStage.ROUTE, WorkflowStage.EXTRACT)
    assert _Backend.calls == [
        ("route", "p1_2019_11"), ("extract", "p1_2019_11"),
        ("hwp", "p1_2019_11"), ("pdf", "p1_2019_11"),
    ]
    assert tuple(
        path.read_bytes()
        for path in (repeated.reports.summary, repeated.reports.failures, repeated.reports.markdown)
    ) == reports_before
    assert repeated.contact_sheet.read_bytes() == contact_before
    assert resumed.results[0].artifact_hash == artifact_hash(resumed.results[0].source_path)
    assert resumed.contact_sheet.stat().st_size > 0
    assert resumed.results[0].source_path.name == "p1_2019_11.pdf"


def test_harness_aggregates_stage_failures_without_aborting_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failures = (
        ("p1_2019_11", WorkflowStage.EXTRACT),
        ("p2_2026_11", WorkflowStage.PDF),
    )
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans, failures))
    options = HarnessOptions(
        MANIFEST,
        tmp_path,
        None,
        False,
        (SourceGroup.KICE,),
        ("p1_2019_11", "p2_2026_11", "c1_2024_06"),
        3,
    )

    result = run_harness(options)

    failures_payload = json.loads(result.reports.failures.read_text(encoding="utf-8"))
    summary = json.loads(result.reports.summary.read_text(encoding="utf-8"))
    assert len(result.results) == 3
    assert {row["stage"] for row in failures_payload["failures"]} == {"extract", "pdf"}
    assert result.contact_sheet.stat().st_size > 0
    with Image.open(result.contact_sheet) as contact:
        assert contact.getpixel((20, 60)) == (255, 0, 0)
    assert any(run.status.value == "succeeded" for run in result.results)
    assert set(summary["sample_groups"]) == {"kice", "ebs", "raster"}
    assert summary["sample_groups"]["kice"]["count"] == 10
    assert summary["sample_groups"]["kice"]["selected_count"] == 3
    assert len(summary["fixed_regression_claims"]) == 7
    assert summary["contact_sheet"] == str(result.contact_sheet)
    assert set(summary["artifact_hashes"]) == {
        "p1_2019_11", "p2_2026_11", "c1_2024_06",
    }


def test_changed_selection_uses_distinct_checkpoint_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans))
    first = HarnessOptions(MANIFEST, tmp_path, 1, False, (), ("p1_2019_11",), 1)
    second = HarnessOptions(MANIFEST, tmp_path, 1, False, (), ("p2_2026_11",), 1)

    run_harness(first)
    first_id = json.loads((tmp_path / "run-metadata.json").read_text())["namespace_id"]
    run_harness(second)
    second_id = json.loads((tmp_path / "run-metadata.json").read_text())["namespace_id"]

    assert first_id != second_id
    assert (tmp_path / "namespaces" / first_id / "checkpoints").is_dir()
    assert (tmp_path / "namespaces" / second_id / "checkpoints").is_dir()


@pytest.mark.parametrize("observed_count", [19, 21])
def test_kice_count_mismatch_is_rejected_before_backend(
    observed_count: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject, "verify_manifest_sources",
        lambda manifest, **_: _verification(manifest, bad_items=frozenset({"p1_2019_11"})),
    )
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans))

    result = run_harness(HarnessOptions(
        MANIFEST, tmp_path / str(observed_count), None, False, (), ("p1_2019_11",), 1,
    ))

    assert result.results[0].failures[0].code == "source_verification_failed"
    assert "items=False" in result.results[0].failures[0].detail


def test_source_hash_mismatch_is_rejected_before_output_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject, "verify_manifest_sources",
        lambda manifest, **_: _verification(manifest, bad_hashes=frozenset({"p1_2019_11"})),
    )
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans))

    result = run_harness(HarnessOptions(
        MANIFEST, tmp_path, None, False, (), ("p1_2019_11",), 1,
    ))

    assert result.results[0].failures[0].code == "source_verification_failed"
    assert not tuple((tmp_path / "namespaces").rglob("sources"))


def test_raster_route_uses_verified_companion_item_for_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BackendSourcePlan, ...]] = []

    def backend(plans: tuple[BackendSourcePlan, ...]) -> _Backend:
        captured.append(plans)
        return _Backend(plans)

    monkeypatch.setattr(subject, "RealRoundTripBackend", backend)

    result = run_harness(HarnessOptions(
        MANIFEST, tmp_path, 1, False, (SourceGroup.RASTER,), (), 1,
    ))

    raster = subject.load_manifest(MANIFEST).raster_fixture
    assert result.results[0].route_kind.value == "raster"
    assert captured[0][0].original_path == raster.path.resolve()
    assert captured[0][0].pipeline_pdf == raster.companion_pdf_path.resolve()
    assert captured[0][0].selected_numbers == (20,)


def test_raster_companion_hash_mismatch_is_rejected_before_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "verify_manifest_sources",
        lambda manifest, **_: _verification(
            manifest, bad_hashes=frozenset({"item20_original:companion"}),
        ),
    )
    monkeypatch.setattr(subject, "RealRoundTripBackend", lambda plans: _Backend(plans))

    result = run_harness(HarnessOptions(
        MANIFEST, tmp_path, None, False, (SourceGroup.RASTER,), (), 1,
    ))

    assert result.results[0].failures[0].code == "source_verification_failed"
    assert "companion" in result.results[0].failures[0].detail
    assert not tuple((tmp_path / "namespaces").rglob("sources"))


def test_parser_exposes_bounded_interruption_flags() -> None:
    parsed = subject.build_parser().parse_args([
        "--manifest", str(MANIFEST),
        "--run-root", "run",
        "--stop-after-completed-stages", "2",
        "--verify-sources",
        "--group", "ebs",
        "--source-id", "ebs_2027_physics1",
        "--max-sources", "1",
    ])

    assert parsed.stop_after_completed_stages == 2
    assert parsed.verify_sources
    assert parsed.group == ["ebs"]
    assert parsed.source_id == ["ebs_2027_physics1"]
    assert parsed.max_sources == 1
