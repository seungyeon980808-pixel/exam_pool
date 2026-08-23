from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pdf_hwp_roundtrip_checkpoint import CheckpointStore, artifact_hash
from app.pdf_hwp_roundtrip_models import (
    SourceFacts,
    SourceIntegrity,
    SourceRoute,
    WorkflowStage,
)
from app.pdf_hwp_roundtrip_reports import write_reports
from app.pdf_hwp_roundtrip_router import route_source
from app.pdf_hwp_roundtrip_runner import (
    BackendStageError,
    ExtractOutcome,
    HwpOutcome,
    PdfOutcome,
    RouteOutcome,
    RoundTripRunner,
    RunPolicy,
    RunStatus,
    SourceInput,
)


class _FakeBackend:
    """File-backed fake whose call journal proves resume and isolation behavior."""

    def __init__(self, output_root: Path, fail_typeset_name: str | None = None) -> None:
        self.output_root = output_root
        self.fail_typeset_name = fail_typeset_name
        self.calls: list[tuple[str, str]] = []

    def route(self, source: SourceInput) -> RouteOutcome:
        self.calls.append(("route", source.path.name))
        return RouteOutcome(route_source(source.facts))

    def extract(self, source: SourceInput, route: SourceRoute) -> ExtractOutcome:
        self.calls.append(("extract", source.path.name))
        target = self.output_root / f"{source.path.stem}-extract.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("extracted", encoding="utf-8")
        return ExtractOutcome(target)

    def typeset(self, source: SourceInput, route: SourceRoute) -> HwpOutcome:
        self.calls.append(("typeset", source.path.name))
        if source.path.name == self.fail_typeset_name:
            raise BackendStageError(WorkflowStage.HWP, "typeset_failed", "synthetic failure")
        bundle = self.output_root / source.path.stem
        bundle.mkdir(parents=True, exist_ok=True)
        target = bundle / "converted.hwp"
        target.write_bytes(b"HWP artifact")
        pdf = bundle / "converted.pdf"
        pdf.write_bytes(b"%PDF-1.7\ngenerated\n%%EOF")
        manifest = bundle / "backend-conversion.json"
        manifest.write_text("conversion paths", encoding="utf-8")
        return HwpOutcome(target, (pdf, manifest))

    def verify(self, source: SourceInput, route: SourceRoute) -> PdfOutcome:
        self.calls.append(("verify", source.path.name))
        bundle = self.output_root / source.path.stem
        target = bundle / "converted.pdf"
        verification = bundle / "verification.json"
        verification.write_text("verified", encoding="utf-8")
        contact = bundle / "item-failures.png"
        contact.write_bytes(b"contact")
        return PdfOutcome(target, (verification, contact))


def _source(tmp_path: Path, filename: str = "p1_2024_11.pdf") -> SourceInput:
    path = tmp_path / filename
    path.write_bytes(f"%PDF-1.7\n{filename}\n%%EOF".encode())
    return SourceInput(path, SourceFacts(
        filename=filename,
        identity_text="2024학년도 대학수학능력시험 문제지",
        source_text="1. 문항",
        page_count=1,
        raster_page_count=0,
        integrity=SourceIntegrity.VALID,
    ))


def test_runner_restarts_after_route_without_recalling_completed_backend(tmp_path: Path) -> None:
    # Given: one source, atomic checkpoint storage, and a one-stage interruption policy.
    source = _source(tmp_path)
    backend = _FakeBackend(tmp_path / "outputs")
    store = CheckpointStore(tmp_path / "state")

    # When: the first process stops after ROUTE and a fresh runner resumes.
    paused = RoundTripRunner(backend, store, RunPolicy(1)).run((source,))[0]
    calls_at_interrupt = tuple(backend.calls)
    resumed = RoundTripRunner(backend, store, RunPolicy()).run((source,))[0]

    # Then: restart begins at EXTRACT and never calls route twice.
    assert paused.status is RunStatus.PAUSED
    assert paused.completed_stages == (WorkflowStage.ROUTE,)
    assert calls_at_interrupt == (("route", source.path.name),)
    assert tuple(name for name, _ in backend.calls) == ("route", "extract", "typeset", "verify")
    assert resumed.status is RunStatus.SUCCEEDED
    assert resumed.completed_stages == (
        WorkflowStage.ROUTE, WorkflowStage.EXTRACT, WorkflowStage.HWP, WorkflowStage.PDF,
    )
    saved = store.load(artifact_hash(source.path))
    assert saved is not None
    assert saved.completed_stages == resumed.completed_stages


def test_resume_retains_completed_artifact_hashes_from_checkpoint(tmp_path: Path) -> None:
    # Given: the first process checkpoints ROUTE and an EXTRACT artifact.
    source = _source(tmp_path)
    backend = _FakeBackend(tmp_path / "outputs")
    store = CheckpointStore(tmp_path / "state")
    first = RoundTripRunner(backend, store, RunPolicy(2)).run((source,))[0]
    extract_hash = first.artifacts[0].artifact_hash

    # When: a fresh runner finishes the remaining stages from disk.
    resumed = RoundTripRunner(backend, store, RunPolicy()).run((source,))[0]

    # Then: the final evidence retains EXTRACT and adds HWP/PDF in stage order.
    assert tuple(artifact.stage for artifact in resumed.artifacts) == (
        WorkflowStage.EXTRACT,
        WorkflowStage.HWP, WorkflowStage.HWP, WorkflowStage.HWP,
        WorkflowStage.PDF, WorkflowStage.PDF, WorkflowStage.PDF,
    )
    assert resumed.artifacts[0].artifact_hash == extract_hash


@pytest.mark.parametrize(
    ("stale_stage", "remove", "expected_calls"),
    [
        (WorkflowStage.EXTRACT, True, ("extract", "typeset", "verify")),
        (WorkflowStage.HWP, False, ("typeset", "verify")),
    ],
)
def test_resume_reruns_stale_stage_and_every_later_stage(
    tmp_path: Path, stale_stage: WorkflowStage, remove: bool, expected_calls: tuple[str, ...],
) -> None:
    # Given: a complete checkpoint whose EXTRACT is deleted or HWP bytes are mutated.
    source = _source(tmp_path)
    backend = _FakeBackend(tmp_path / "outputs")
    store = CheckpointStore(tmp_path / "state")
    complete = RoundTripRunner(backend, store, RunPolicy()).run((source,))[0]
    stale = next(artifact for artifact in complete.artifacts if artifact.stage is stale_stage)
    if remove:
        stale.path.unlink()
    else:
        stale.path.write_bytes(b"mutated artifact")
    backend.calls.clear()

    # When: resume validates persisted evidence before trusting completed stages.
    resumed = RoundTripRunner(backend, store, RunPolicy()).run((source,))[0]

    # Then: work restarts at the first stale stage and refreshes every later hash.
    assert tuple(name for name, _ in backend.calls) == expected_calls
    assert resumed.status is RunStatus.SUCCEEDED
    assert tuple(artifact.stage for artifact in resumed.artifacts) == (
        WorkflowStage.EXTRACT,
        WorkflowStage.HWP, WorkflowStage.HWP, WorkflowStage.HWP,
        WorkflowStage.PDF, WorkflowStage.PDF, WorkflowStage.PDF,
    )
    assert all(artifact.path.is_file() for artifact in resumed.artifacts)


@pytest.mark.parametrize(("artifact_name", "stage", "expected_calls"), (
    ("converted.pdf", WorkflowStage.HWP, ("typeset", "verify")),
    ("backend-conversion.json", WorkflowStage.HWP, ("typeset", "verify")),
    ("verification.json", WorkflowStage.PDF, ("verify",)),
    ("item-failures.png", WorkflowStage.PDF, ("verify",)),
))
def test_resume_reruns_when_any_same_stage_auxiliary_is_missing(
    tmp_path: Path,
    artifact_name: str,
    stage: WorkflowStage,
    expected_calls: tuple[str, ...],
) -> None:
    # Given: a complete checkpoint whose real-shaped bundle loses one auxiliary.
    source = _source(tmp_path)
    backend = _FakeBackend(tmp_path / "outputs")
    store = CheckpointStore(tmp_path / "state")
    complete = RoundTripRunner(backend, store, RunPolicy()).run((source,))[0]
    auxiliary = next(
        artifact for artifact in complete.artifacts
        if artifact.stage is stage and artifact.path.name == artifact_name
    )
    auxiliary.path.unlink()
    backend.calls.clear()

    # When: restart validates every stored member of the stage bundle.
    RoundTripRunner(backend, store, RunPolicy()).run((source,))

    # Then: work restarts at the first stage whose auxiliary disappeared.
    assert tuple(name for name, _ in backend.calls) == expected_calls


def test_unknown_source_is_quarantined_without_hwp_call(tmp_path: Path) -> None:
    # Given: a signature-free source that routes UNKNOWN.
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"unknown")
    source = SourceInput(path, SourceFacts(
        "notes.pdf", "notes", "plain", 1, 0, SourceIntegrity.VALID,
    ))
    backend = _FakeBackend(tmp_path / "outputs")

    # When: the runner handles its route.
    result = RoundTripRunner(backend, CheckpointStore(tmp_path / "state"), RunPolicy()).run((source,))[0]

    # Then: quarantine is durable and no conversion backend stage runs.
    assert result.status is RunStatus.QUARANTINED
    assert result.completed_stages == (WorkflowStage.ROUTE, WorkflowStage.QUARANTINE)
    assert backend.calls == [("route", "notes.pdf")]


def test_backend_failure_preserves_prior_stages_and_other_source_succeeds(tmp_path: Path) -> None:
    # Given: two sources where only one typeset stage fails.
    failing = _source(tmp_path, "p1_2023_11.pdf")
    passing = _source(tmp_path, "p1_2024_11.pdf")
    backend = _FakeBackend(tmp_path / "outputs", failing.path.name)

    # When: both are run in one deterministic batch.
    results = RoundTripRunner(
        backend, CheckpointStore(tmp_path / "state"), RunPolicy(),
    ).run((failing, passing))

    # Then: the failure retains ROUTE/EXTRACT while its sibling completes.
    assert tuple(result.status for result in results) == (RunStatus.FAILED, RunStatus.SUCCEEDED)
    assert results[0].completed_stages == (WorkflowStage.ROUTE, WorkflowStage.EXTRACT)
    assert results[0].failures[0].code == "typeset_failed"
    assert results[1].completed_stages[-1] is WorkflowStage.PDF


def test_corrupt_checkpoint_fails_only_its_source_without_deleting_evidence(tmp_path: Path) -> None:
    # Given: one source has corrupt persisted JSON while its sibling has no checkpoint.
    corrupt = _source(tmp_path, "p1_2023_11.pdf")
    passing = _source(tmp_path, "p1_2024_11.pdf")
    state = tmp_path / "state"
    state.mkdir()
    corrupt_path = state / f"{artifact_hash(corrupt.path)}.json"
    corrupt_path.write_text("{not valid JSON", encoding="utf-8")
    backend = _FakeBackend(tmp_path / "outputs")

    # When: both sources run in one batch.
    results = RoundTripRunner(backend, CheckpointStore(state), RunPolicy()).run((corrupt, passing))

    # Then: corruption is typed and preserved while the sibling completes normally.
    assert tuple(result.status for result in results) == (RunStatus.FAILED, RunStatus.SUCCEEDED)
    assert results[0].failures[0].code == "checkpoint_corrupt"
    assert corrupt_path.read_text(encoding="utf-8") == "{not valid JSON"
    assert tuple(name for name, _ in backend.calls) == ("route", "extract", "typeset", "verify")


def test_report_writer_emits_nonempty_hash_keyed_deterministic_files(tmp_path: Path) -> None:
    # Given: one successful and one failed real file-backed fake run.
    failing = _source(tmp_path, "p1_2023_11.pdf")
    passing = _source(tmp_path, "p1_2024_11.pdf")
    result = RoundTripRunner(
        _FakeBackend(tmp_path / "outputs", failing.path.name),
        CheckpointStore(tmp_path / "state"),
        RunPolicy(),
    ).run((failing, passing))

    # When: deterministic reports are atomically written.
    paths = write_reports(result, tmp_path / "reports")

    # Then: all formats are non-empty and identify the exact source hash and groups.
    assert all(path.stat().st_size > 0 for path in (paths.summary, paths.failures, paths.markdown))
    summary = json.loads(paths.summary.read_text(encoding="utf-8"))
    assert {source["artifact_hash"] for source in summary["sources"]} == {
        artifact_hash(failing.path), artifact_hash(passing.path),
    }
    assert summary["groups"] == [
        {"status": "succeeded", "count": 1},
        {"status": "failed", "count": 1},
    ]
    failures = paths.failures.read_text(encoding="utf-8")
    markdown = paths.markdown.read_text(encoding="utf-8")
    assert artifact_hash(failing.path) in failures
    assert all(artifact_hash(source.path) in markdown for source in (failing, passing))
