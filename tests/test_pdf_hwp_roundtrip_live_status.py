from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.pdf_hwp_roundtrip_live_status as subject
from app.pdf_hwp_roundtrip_models import (
    ArtifactHash,
    PersistedStageArtifact,
    QuarantineReason,
    SourceFacts,
    SourceIntegrity,
    SourceKind,
    SourceProfile,
    UnknownRoute,
    WorkflowStage,
)
from app.pdf_hwp_roundtrip_runner import (
    RunFailure,
    RouteOutcome,
    RoundTripRunner,
    RunPolicy,
    RunStatus,
    SourceRunResult,
    SourceInput,
)
from app.pdf_hwp_roundtrip_checkpoint import CheckpointStore, artifact_hash
from tools.pdf_hwp_roundtrip_live_status import LiveSource, run_with_live_status


def test_live_status_retries_transient_atomic_publish_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace = subject.os.replace
    calls = 0

    def fail_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "simulated live-status deny", target)
        replace(source, target)

    monkeypatch.setattr(subject.os, "replace", fail_once)

    target = subject.write_live_status(
        tmp_path, "namespace", (), (), state="running",
    )

    assert calls == 3
    assert target.is_file()
    assert not tuple(tmp_path.glob(".live-status.json.*.tmp"))


def test_live_status_publishes_hash_verified_hwp_after_pdf_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = (tmp_path / "source.pdf").resolve()
    source_path.write_bytes(b"source")
    hwp = (tmp_path / "namespace" / "converted.hwp").resolve()
    hwp.parent.mkdir()
    hwp.write_bytes(b"reviewable-hwp")
    hwp_hash = artifact_hash(hwp)
    source_hash = ArtifactHash("b" * 64)
    result = SourceRunResult(
        source_path,
        source_hash,
        SourceKind.KICE,
        RunStatus.FAILED,
        (WorkflowStage.ROUTE, WorkflowStage.EXTRACT, WorkflowStage.HWP),
        (PersistedStageArtifact(WorkflowStage.HWP, hwp, hwp_hash),),
        (RunFailure(source_hash, source_path, WorkflowStage.PDF, "pdf_failed", "failed"),),
    )
    live = LiveSource(
        "safe_source", source_path, tmp_path / "output", SourceProfile.KICE_STRUCTURAL,
    )

    target = subject.write_live_status(
        tmp_path / "run", "namespace-2", (live,), (result,), state="complete",
    )
    index_path = tmp_path / "run" / "intermediate-hwp" / "index.json"
    review = tmp_path / "run" / "intermediate-hwp" / "safe_source.hwp"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(subject, "copyfile", lambda *_: pytest.fail("same hash recopied"))
    subject.write_live_status(
        tmp_path / "run", "namespace-2", (live,), (result,), state="complete",
    )

    assert target.is_file()
    assert review.read_bytes() == hwp.read_bytes()
    assert index["namespace_id"] == "namespace-2"
    assert index["authoritative_artifacts_unchanged"] is True
    assert index["sources"][0]["source_sha256"] == source_hash
    assert index["sources"][0]["hwp_ready"] is True
    assert index["sources"][0]["pdf_ready"] is False
    assert index["sources"][0]["artifacts"]["hwp"]["sha256"] == hwp_hash


def test_intermediate_review_selects_documents_not_stage_auxiliaries(tmp_path: Path) -> None:
    source_path = (tmp_path / "source.pdf").resolve()
    source_path.write_bytes(b"source")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    hwp = bundle / "converted.hwp"
    hwp.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"hwp-document")
    hwp_preview = bundle / "hwp-preview.pdf"
    hwp_preview.write_bytes(b"%PDF-hwp-preview")
    backend_json = bundle / "conversion.json"
    backend_json.write_text("{}", encoding="utf-8")
    generated_pdf = bundle / "generated.pdf"
    generated_pdf.write_bytes(b"%PDF-generated-document")
    verification_json = bundle / "verification.json"
    verification_json.write_text("{}", encoding="utf-8")
    profile_json = bundle / "profile-verification.json"
    profile_json.write_text("{}", encoding="utf-8")
    contact_png = bundle / "contact.png"
    contact_png.write_bytes(b"PNG-auxiliary")
    source_hash = ArtifactHash("c" * 64)
    artifacts = tuple(
        PersistedStageArtifact(stage, path, artifact_hash(path))
        for stage, path in (
            (WorkflowStage.HWP, hwp),
            (WorkflowStage.HWP, hwp_preview),
            (WorkflowStage.HWP, backend_json),
            (WorkflowStage.PDF, generated_pdf),
            (WorkflowStage.PDF, verification_json),
            (WorkflowStage.PDF, profile_json),
            (WorkflowStage.PDF, contact_png),
        )
    )
    result = SourceRunResult(
        source_path, source_hash, SourceKind.KICE, RunStatus.SUCCEEDED,
        tuple(WorkflowStage), artifacts,
    )
    live = LiveSource(
        "bundle_source", source_path, bundle, SourceProfile.KICE_STRUCTURAL,
    )

    subject.write_live_status(
        tmp_path / "run", "namespace-3", (live,), (result,), state="complete",
    )
    review_root = tmp_path / "run" / "intermediate-hwp"
    index = json.loads((review_root / "index.json").read_text(encoding="utf-8"))
    row = index["sources"][0]

    assert (review_root / "bundle_source.hwp").read_bytes() == hwp.read_bytes()
    assert (review_root / "bundle_source.pdf").read_bytes() == generated_pdf.read_bytes()
    assert row["artifacts"]["hwp"]["sha256"] == artifact_hash(hwp)
    assert row["artifacts"]["pdf"]["sha256"] == artifact_hash(generated_pdf)


def test_intermediate_review_rejects_duplicate_hwp_documents(tmp_path: Path) -> None:
    source_path = (tmp_path / "source.pdf").resolve()
    source_path.write_bytes(b"source")
    first = tmp_path / "first.hwp"
    second = tmp_path / "second.hwp"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    source_hash = ArtifactHash("d" * 64)
    result = SourceRunResult(
        source_path, source_hash, SourceKind.KICE, RunStatus.FAILED,
        (WorkflowStage.HWP,),
        tuple(PersistedStageArtifact(
            WorkflowStage.HWP, path, artifact_hash(path),
        ) for path in (first, second)),
    )
    live = LiveSource(
        "duplicate_source", source_path, tmp_path, SourceProfile.KICE_STRUCTURAL,
    )

    with pytest.raises(subject.AmbiguousReviewArtifactError, match="2 candidate hwp"):
        subject.write_live_status(
            tmp_path / "run", "namespace-4", (live,), (result,), state="complete",
        )


class _QuarantineBackend:
    def route(self, _source: SourceInput) -> RouteOutcome:
        return RouteOutcome(UnknownRoute(QuarantineReason.UNRECOGNIZED))

    def extract(self, *_args):  # pragma: no cover - quarantine has no extract stage
        raise AssertionError

    def typeset(self, *_args):  # pragma: no cover - quarantine has no HWP stage
        raise AssertionError

    def verify(self, *_args):  # pragma: no cover - quarantine has no PDF stage
        raise AssertionError


def test_live_status_is_atomic_complete_and_links_intermediate_outputs(tmp_path: Path) -> None:
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"not-a-pdf")
    source = SourceInput(source_path, SourceFacts(
        source_path, ArtifactHash("a" * 64), SourceKind.UNKNOWN, 1, 0,
        SourceIntegrity.MALFORMED,
    ))
    live = LiveSource(
        "unknown", source_path, tmp_path / "outputs", SourceProfile.KICE_STRUCTURAL,
    )
    runner = RoundTripRunner(
        _QuarantineBackend(), CheckpointStore(tmp_path / "checkpoints"), RunPolicy(),
    )

    results = run_with_live_status(
        runner, (source,), (live,), (), tmp_path / "run", "namespace-1",
    )

    assert results[0].status is RunStatus.QUARANTINED
    payload = json.loads((tmp_path / "run" / "live-status.json").read_text(encoding="utf-8"))
    assert payload["state"] == "complete"
    assert payload["counts"]["quarantined"] == 1
    assert payload["sources"][0]["completed_stages"] == ["route", "quarantine"]
    assert payload["sources"][0]["profile"] == "kice_structural"
    assert payload["latest_reports"]["contact_sheet"].endswith("contact-sheet.png")
    assert not tuple((tmp_path / "run").glob(".live-status.json.*.tmp"))
