from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.pdf_hwp_atomic as atomic_subject
from app.pdf_hwp_roundtrip_checkpoint import (
    CheckpointStore,
    artifact_hash,
    next_pending_stage,
)
from app.pdf_hwp_roundtrip_models import (
    ArtifactCheckpoint,
    KiceRoute,
    PersistedStageArtifact,
    SourceFacts,
    SourceIntegrity,
    SourceKind,
    WorkflowStage,
)
from app.pdf_hwp_roundtrip_router import route_source, scheduled_stages


def _checkpoint_fixture(tmp_path: Path) -> ArtifactCheckpoint:
    source = tmp_path / "retry-source.pdf"
    source.write_bytes(b"checkpoint retry")
    return ArtifactCheckpoint(
        artifact_hash(source), KiceRoute(), (WorkflowStage.ROUTE,),
    )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            SourceFacts(
                filename="p1_2024_11.pdf",
                identity_text="2024학년도 대학수학능력시험 문제지",
                source_text="20. 다음은 물리학 문항이다.",
                page_count=4,
                raster_page_count=0,
                integrity=SourceIntegrity.VALID,
            ),
            SourceKind.KICE,
        ),
        (
            SourceFacts(
                filename="2027 수능특강 물리학 I 원본.pdf",
                identity_text="EBS 수능특강 물리학 I",
                source_text="[26023-0001] 01 다음 문제를 풀어 보자.",
                page_count=200,
                raster_page_count=0,
                integrity=SourceIntegrity.VALID,
            ),
            SourceKind.EBS,
        ),
        (
            SourceFacts(
                filename="question-image.pdf",
                identity_text="",
                source_text="[raster source page 1]",
                page_count=1,
                raster_page_count=1,
                integrity=SourceIntegrity.VALID,
            ),
            SourceKind.RASTER,
        ),
    ],
)
def test_route_source_selects_typed_route_when_signature_is_proven(
    facts: SourceFacts, expected: SourceKind,
) -> None:
    # Given: source facts pinned from the existing KICE, EBS, and raster paths.
    # When: the source router classifies the facts.
    route = route_source(facts)
    # Then: the proven source family is selected and HWP is scheduled.
    assert route.kind is expected
    assert WorkflowStage.HWP in scheduled_stages(route)


@pytest.mark.parametrize("integrity", [SourceIntegrity.VALID, SourceIntegrity.MALFORMED])
def test_unknown_or_malformed_source_is_quarantined_without_hwp(
    integrity: SourceIntegrity,
) -> None:
    # Given: a malformed or signature-free source.
    facts = SourceFacts(
        filename="unknown.pdf",
        identity_text="unrecognized document",
        source_text="no known source signature",
        page_count=1,
        raster_page_count=0,
        integrity=integrity,
    )
    # When: the source router classifies the facts.
    route = route_source(facts)
    # Then: UNKNOWN is quarantined and can never enqueue HWP.
    assert route.kind is SourceKind.UNKNOWN
    assert route.quarantined is True
    assert scheduled_stages(route) == (WorkflowStage.QUARANTINE,)


def test_atomic_checkpoint_reloads_by_hash_and_resumes_next_stage(tmp_path: Path) -> None:
    # Given: a real artifact and a completed ROUTE checkpoint.
    artifact = tmp_path / "source.pdf"
    artifact.write_bytes(b"%PDF-1.7\nroundtrip fixture\n%%EOF\n")
    stable_hash = artifact_hash(artifact)
    route = route_source(SourceFacts(
        filename="p1_2024_11.pdf",
        identity_text="2024학년도 대학수학능력시험 문제지",
        source_text="1. 문항",
        page_count=1,
        raster_page_count=0,
        integrity=SourceIntegrity.VALID,
    ))
    checkpoint = ArtifactCheckpoint(
        artifact_hash=stable_hash,
        route=route,
        completed_stages=(WorkflowStage.ROUTE,),
    )

    # When: an interrupted process saves atomically and a new store reloads it.
    checkpoint_path = CheckpointStore(tmp_path / "state").save(checkpoint)
    resumed = CheckpointStore(tmp_path / "state").load(stable_hash)

    # Then: the hash key and next pending stage survive the restart without temp debris.
    assert resumed == checkpoint
    assert resumed is not None
    assert resumed.artifact_hash == artifact_hash(artifact)
    assert next_pending_stage(resumed) is WorkflowStage.EXTRACT
    assert checkpoint_path.name == f"{stable_hash}.json"
    assert list(checkpoint_path.parent.glob("*.tmp")) == []


def test_checkpoint_replace_retries_once_after_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = _checkpoint_fixture(tmp_path)
    state = tmp_path / "state"
    replace = atomic_subject.os.replace
    calls = 0
    delays: list[float] = []

    def fail_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "simulated transient deny", target)
        replace(source, target)

    monkeypatch.setattr(atomic_subject.os, "replace", fail_once)
    monkeypatch.setattr(atomic_subject, "sleep", delays.append)

    target = CheckpointStore(state).save(checkpoint)

    assert calls == 2
    assert delays == [atomic_subject._REPLACE_RETRY_SECONDS]
    assert target.is_file()
    assert list(state.glob(".*.tmp")) == []


def test_checkpoint_persists_stage_artifact_path_and_hash(tmp_path: Path) -> None:
    # Given: EXTRACT completed with a real manifest artifact.
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    manifest = tmp_path / "extract.json"
    manifest.write_text("evidence", encoding="utf-8")
    digest = artifact_hash(source)
    evidence = PersistedStageArtifact(
        WorkflowStage.EXTRACT, manifest.resolve(), artifact_hash(manifest),
    )
    checkpoint = ArtifactCheckpoint(
        digest, KiceRoute(), (WorkflowStage.ROUTE, WorkflowStage.EXTRACT), (evidence,),
    )

    # When: state is atomically saved and loaded through a new store.
    store = CheckpointStore(tmp_path / "state")
    target = store.save(checkpoint)
    loaded = CheckpointStore(tmp_path / "state").load(digest)

    # Then: exact artifact evidence survives the JSON boundary.
    assert loaded == checkpoint
    assert evidence.artifact_hash in target.read_text(encoding="utf-8")


def test_checkpoint_loads_legacy_json_without_artifacts(tmp_path: Path) -> None:
    # Given: checkpoint JSON written by the prior schema without an artifacts member.
    source = tmp_path / "source.pdf"
    source.write_bytes(b"legacy")
    digest = artifact_hash(source)
    checkpoint = ArtifactCheckpoint(digest, KiceRoute(), (WorkflowStage.ROUTE,))
    store = CheckpointStore(tmp_path / "state")
    target = store.save(checkpoint)
    payload = json.loads(target.read_text(encoding="utf-8"))
    del payload["artifacts"]
    target.write_text(json.dumps(payload), encoding="utf-8")

    # When: the current reader loads the old payload.
    loaded = CheckpointStore(tmp_path / "state").load(digest)

    # Then: backward compatibility supplies an empty evidence tuple.
    assert loaded == checkpoint
