"""Atomic source-level progress view for long round-trip harness runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from shutil import copyfile
from typing import Final
from uuid import uuid4

from app.pdf_hwp_atomic import atomic_replace
from app.pdf_hwp_roundtrip_checkpoint import artifact_hash
from app.pdf_hwp_roundtrip_models import PersistedStageArtifact, SourceProfile, WorkflowStage
from app.pdf_hwp_roundtrip_runner import RoundTripRunner, RunStatus, SourceInput, SourceRunResult
from tools.pdf_hwp_roundtrip_harness_contract import Candidate


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
_SAFE_SOURCE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class LiveSource:
    source_id: str
    source_path: Path
    output_dir: Path
    profile: SourceProfile


@dataclass(frozen=True, slots=True)
class UnsafeReviewSourceIdError(ValueError):
    source_id: str

    def __str__(self) -> str:
        return f"source id is unsafe for a review filename: {self.source_id!r}"


@dataclass(frozen=True, slots=True)
class ReviewArtifactHashError(ValueError):
    path: Path
    expected: str
    actual: str

    def __str__(self) -> str:
        return f"review artifact hash mismatch for {self.path}: {self.actual} != {self.expected}"


@dataclass(frozen=True, slots=True)
class AmbiguousReviewArtifactError(ValueError):
    source_id: str
    kind: str
    count: int

    def __str__(self) -> str:
        return f"source {self.source_id} has {self.count} candidate {self.kind} artifacts"


def _atomic_json(path: Path, payload: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, path)


def _review_copy(authoritative: Path, target: Path, expected_hash: str) -> Path:
    actual = artifact_hash(authoritative)
    if actual != expected_hash:
        raise ReviewArtifactHashError(authoritative, expected_hash, actual)
    if target.is_file() and artifact_hash(target) == expected_hash:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        copyfile(authoritative, temporary)
        copied_hash = artifact_hash(temporary)
        if copied_hash != expected_hash:
            raise ReviewArtifactHashError(temporary, expected_hash, copied_hash)
        atomic_replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _review_artifacts(
    source_id: str, result: SourceRunResult | None,
) -> tuple[tuple[str, PersistedStageArtifact], ...]:
    if result is None:
        return ()
    selected: list[tuple[str, PersistedStageArtifact]] = []
    for kind, stage, suffix in (
        ("hwp", WorkflowStage.HWP, ".hwp"),
        ("pdf", WorkflowStage.PDF, ".pdf"),
    ):
        candidates = tuple(
            artifact for artifact in result.artifacts
            if artifact.stage is stage and artifact.path.suffix.lower() == suffix
        )
        if len(candidates) > 1:
            raise AmbiguousReviewArtifactError(source_id, kind, len(candidates))
        if candidates:
            selected.append((kind, candidates[0]))
    return tuple(selected)


def _publish_intermediate_index(
    run_root: Path,
    namespace_id: str,
    sources: tuple[LiveSource, ...],
    results: tuple[SourceRunResult, ...],
) -> Path:
    review_root = run_root.resolve() / "intermediate-hwp"
    by_path = {result.source_path.resolve(): result for result in results}
    rows: list[JsonObject] = []
    for source in sources:
        if _SAFE_SOURCE_ID.fullmatch(source.source_id) is None:
            raise UnsafeReviewSourceIdError(source.source_id)
        result = by_path.get(source.source_path.resolve())
        review_paths = {
            "hwp": review_root / f"{source.source_id}.hwp",
            "pdf": review_root / f"{source.source_id}.pdf",
        }
        published: dict[str, JsonObject] = {}
        for kind, artifact in _review_artifacts(source.source_id, result):
            review_path = _review_copy(
                artifact.path.resolve(), review_paths[kind], artifact.artifact_hash,
            )
            published[kind] = {
                "review_copy": True,
                "authoritative_path": str(artifact.path.resolve()),
                "review_path": str(review_path.resolve()),
                "sha256": artifact.artifact_hash,
            }
        rows.append({
            "source_id": source.source_id,
            "namespace_id": namespace_id,
            "source_sha256": None if result is None else result.artifact_hash,
            "hwp_ready": "hwp" in published,
            "pdf_ready": "pdf" in published,
            "hwp_review_path": str(review_paths["hwp"].resolve()),
            "pdf_review_path": str(review_paths["pdf"].resolve()),
            "artifacts": published,
        })
    target = review_root / "index.json"
    _atomic_json(target, {
        "schema_version": 1,
        "namespace_id": namespace_id,
        "review_copy": True,
        "authoritative_artifacts_unchanged": True,
        "sources": rows,
    })
    return target


def _source_row(source: LiveSource, result: SourceRunResult | None) -> JsonObject:
    row: JsonObject = {
        "source_id": source.source_id,
        "source_path": str(source.source_path.resolve()),
        "output_dir": str(source.output_dir.resolve()),
        "profile": source.profile.value,
        "status": "queued" if result is None else result.status.value,
        "completed_stages": [] if result is None else [stage.value for stage in result.completed_stages],
        "artifacts": [] if result is None else [
            {
                "stage": artifact.stage.value,
                "path": str(artifact.path),
                "sha256": artifact.artifact_hash,
            }
            for artifact in result.artifacts
        ],
        "failures": [] if result is None else [
            {"stage": failure.stage.value, "code": failure.code, "detail": failure.detail}
            for failure in result.failures
        ],
    }
    return row


def live_sources(
    candidates: tuple[Candidate, ...], output_roots: dict[Path, Path],
) -> tuple[LiveSource, ...]:
    """Bind approved candidates to their inspectable per-source output roots."""
    return tuple(LiveSource(
        candidate.source_id, candidate.path,
        output_roots[candidate.path.resolve()], candidate.profile,
    ) for candidate in candidates)


def write_live_status(
    run_root: Path,
    namespace_id: str,
    sources: tuple[LiveSource, ...],
    results: tuple[SourceRunResult, ...],
    *,
    state: str,
    current_source_id: str | None = None,
) -> Path:
    """Publish one self-contained progress snapshot safe to read during conversion."""
    intermediate_index = _publish_intermediate_index(
        run_root, namespace_id, sources, results,
    )
    by_path = {result.source_path.resolve(): result for result in results}
    rows = [_source_row(source, by_path.get(source.source_path.resolve())) for source in sources]
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("queued", *(value.value for value in RunStatus))
    }
    target = run_root.resolve() / "live-status.json"
    _atomic_json(target, {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "namespace_id": namespace_id,
        "current_source_id": current_source_id,
        "total_sources": len(sources),
        "counts": counts,
        "sources": rows,
        "latest_reports": {
            "summary": str(run_root.resolve() / "summary.json"),
            "failures": str(run_root.resolve() / "failures.json"),
            "markdown": str(run_root.resolve() / "run-report.md"),
            "contact_sheet": str(run_root.resolve() / "contact-sheet.png"),
            "intermediate_index": str(intermediate_index.resolve()),
        },
    })
    return target


def run_with_live_status(
    runner: RoundTripRunner,
    inputs: tuple[SourceInput, ...],
    sources: tuple[LiveSource, ...],
    initial_results: tuple[SourceRunResult, ...],
    run_root: Path,
    namespace_id: str,
) -> tuple[SourceRunResult, ...]:
    """Run independently and refresh the readable status after every source."""
    completed = list(initial_results)
    write_live_status(run_root, namespace_id, sources, tuple(completed), state="running")
    source_id_by_path = {source.source_path.resolve(): source.source_id for source in sources}
    for source in inputs:
        source_id = source_id_by_path[source.path.resolve()]
        write_live_status(
            run_root, namespace_id, sources, tuple(completed),
            state="running", current_source_id=source_id,
        )
        completed.extend(runner.run((source,)))
        write_live_status(run_root, namespace_id, sources, tuple(completed), state="running")
    write_live_status(run_root, namespace_id, sources, tuple(completed), state="complete")
    return tuple(completed)
