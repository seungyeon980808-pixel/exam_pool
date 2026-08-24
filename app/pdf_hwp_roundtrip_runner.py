"""Resumable per-source orchestration for the PDF/HWP round-trip harness."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, assert_never

from .pdf_hwp_roundtrip_checkpoint import CheckpointStore, CorruptCheckpointError, artifact_hash
from .pdf_hwp_roundtrip_models import (
    ArtifactCheckpoint,
    ArtifactHash,
    PersistedStageArtifact,
    SourceFacts,
    SourceKind,
    SourceRoute,
    WorkflowStage,
)
from .pdf_hwp_roundtrip_router import scheduled_stages


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class SourceInput:
    path: Path
    facts: SourceFacts


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    route: SourceRoute


@dataclass(frozen=True, slots=True)
class ExtractOutcome:
    manifest_path: Path
    auxiliary_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class HwpOutcome:
    hwp_path: Path
    auxiliary_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class PdfOutcome:
    pdf_path: Path
    auxiliary_paths: tuple[Path, ...] = ()


class RoundTripBackend(Protocol):
    """Heavy stage adapter implemented outside this orchestration unit."""

    def route(self, source: SourceInput) -> RouteOutcome: ...

    def extract(self, source: SourceInput, route: SourceRoute) -> ExtractOutcome: ...

    def typeset(self, source: SourceInput, route: SourceRoute) -> HwpOutcome: ...

    def verify(self, source: SourceInput, route: SourceRoute) -> PdfOutcome: ...


@dataclass(frozen=True, slots=True)
class BackendStageError(RuntimeError):
    stage: WorkflowStage
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.stage.value}:{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class InvalidRunPolicyError(ValueError):
    completed_stages: int

    def __str__(self) -> str:
        return f"stop_after_completed_stages must be positive, got {self.completed_stages}"


@dataclass(frozen=True, slots=True)
class InvalidExecutionStageError(RuntimeError):
    stage: WorkflowStage

    def __str__(self) -> str:
        return f"stage {self.stage.value} cannot be executed as a backend stage"


@dataclass(frozen=True, slots=True)
class RunPolicy:
    stop_after_completed_stages: int | None = None

    def __post_init__(self) -> None:
        if self.stop_after_completed_stages is not None and self.stop_after_completed_stages <= 0:
            raise InvalidRunPolicyError(self.stop_after_completed_stages)

    def stops_after(self, completed: int) -> bool:
        return self.stop_after_completed_stages is not None and completed >= self.stop_after_completed_stages


StageArtifact = PersistedStageArtifact


@dataclass(frozen=True, slots=True)
class RunFailure:
    artifact_hash: ArtifactHash
    source_path: Path
    stage: WorkflowStage
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    source_path: Path
    artifact_hash: ArtifactHash
    route_kind: SourceKind | None
    status: RunStatus
    completed_stages: tuple[WorkflowStage, ...]
    artifacts: tuple[StageArtifact, ...] = ()
    failures: tuple[RunFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class _FailureContext:
    digest: ArtifactHash
    route: SourceRoute | None
    completed: tuple[WorkflowStage, ...]
    artifacts: tuple[StageArtifact, ...]


class RoundTripRunner:
    """Advance each hash-keyed source and checkpoint every successful stage."""

    def __init__(
        self, backend: RoundTripBackend, checkpoints: CheckpointStore, policy: RunPolicy,
    ) -> None:
        self._backend = backend
        self._checkpoints = checkpoints
        self._policy = policy

    def run(self, sources: tuple[SourceInput, ...]) -> tuple[SourceRunResult, ...]:
        """Run sources independently so one typed backend failure cannot erase siblings."""
        return tuple(self._run_source(source) for source in sources)

    def _failed(
        self, source: SourceInput, context: _FailureContext, error: BackendStageError,
    ) -> SourceRunResult:
        failure = RunFailure(
            context.digest, source.path.resolve(), error.stage, error.code, error.detail,
        )
        return SourceRunResult(
            source.path.resolve(), context.digest,
            None if context.route is None else context.route.kind,
            RunStatus.FAILED, context.completed, context.artifacts, (failure,),
        )

    def _run_source(self, source: SourceInput) -> SourceRunResult:
        resolved = SourceInput(source.path.resolve(), source.facts)
        digest = artifact_hash(resolved.path)
        try:
            checkpoint = self._checkpoints.load(digest)
        except CorruptCheckpointError as exc:
            return self._failed(
                resolved,
                _FailureContext(digest, None, (), ()),
                BackendStageError(WorkflowStage.ROUTE, "checkpoint_corrupt", str(exc)),
            )
        if checkpoint is not None:
            validated = self._validated_checkpoint(checkpoint)
            if validated != checkpoint:
                self._checkpoints.save(validated)
            checkpoint = validated
        completed = () if checkpoint is None else checkpoint.completed_stages
        artifacts = () if checkpoint is None else checkpoint.artifacts
        newly_completed = 0
        if checkpoint is None:
            try:
                route = self._backend.route(resolved).route
            except BackendStageError as exc:
                return self._failed(
                    resolved, _FailureContext(digest, None, completed, artifacts), exc,
                )
            completed = (*completed, WorkflowStage.ROUTE)
            self._checkpoints.save(ArtifactCheckpoint(digest, route, completed, artifacts))
            newly_completed += 1
            if self._policy.stops_after(newly_completed):
                return SourceRunResult(
                    resolved.path, digest, route.kind, RunStatus.PAUSED, completed, artifacts,
                )
        else:
            route = checkpoint.route
        for stage in scheduled_stages(route):
            if stage in completed:
                continue
            try:
                artifact_paths = self._execute_stage(stage, resolved, route)
            except BackendStageError as exc:
                return self._failed(
                    resolved, _FailureContext(digest, route, completed, artifacts), exc,
                )
            if artifact_paths is not None:
                stage_artifacts = tuple(
                    StageArtifact(stage, path.resolve(), artifact_hash(path.resolve()))
                    for path in artifact_paths
                )
                artifacts = (*artifacts, *stage_artifacts)
            completed = (*completed, stage)
            self._checkpoints.save(ArtifactCheckpoint(digest, route, completed, artifacts))
            newly_completed += 1
            if self._policy.stops_after(newly_completed):
                return SourceRunResult(
                    resolved.path, digest, route.kind, RunStatus.PAUSED, completed, artifacts,
                )
        status = RunStatus.QUARANTINED if route.quarantined else RunStatus.SUCCEEDED
        return SourceRunResult(resolved.path, digest, route.kind, status, completed, artifacts)

    def _validated_checkpoint(self, checkpoint: ArtifactCheckpoint) -> ArtifactCheckpoint:
        completed = (WorkflowStage.ROUTE,) if WorkflowStage.ROUTE in checkpoint.completed_stages else ()
        artifacts: tuple[StageArtifact, ...] = ()
        for stage in scheduled_stages(checkpoint.route):
            if stage not in checkpoint.completed_stages:
                break
            match stage:
                case WorkflowStage.EXTRACT | WorkflowStage.HWP | WorkflowStage.PDF:
                    evidence = tuple(
                        artifact for artifact in checkpoint.artifacts if artifact.stage is stage
                    )
                    if not evidence or not all(self._valid_artifact(artifact) for artifact in evidence):
                        break
                    completed = (*completed, stage)
                    artifacts = (*artifacts, *evidence)
                case WorkflowStage.QUARANTINE:
                    completed = (*completed, stage)
                case WorkflowStage.ROUTE:
                    raise InvalidExecutionStageError(stage)
                case unreachable:
                    assert_never(unreachable)
        return ArtifactCheckpoint(
            checkpoint.artifact_hash, checkpoint.route, completed, artifacts,
        )

    @staticmethod
    def _valid_artifact(evidence: StageArtifact) -> bool:
        try:
            return evidence.path.is_file() and artifact_hash(evidence.path) == evidence.artifact_hash
        except OSError:
            return False

    def _execute_stage(
        self, stage: WorkflowStage, source: SourceInput, route: SourceRoute,
    ) -> tuple[Path, ...] | None:
        match stage:
            case WorkflowStage.EXTRACT:
                outcome = self._backend.extract(source, route)
                return (outcome.manifest_path, *outcome.auxiliary_paths)
            case WorkflowStage.HWP:
                outcome = self._backend.typeset(source, route)
                return (outcome.hwp_path, *outcome.auxiliary_paths)
            case WorkflowStage.PDF:
                outcome = self._backend.verify(source, route)
                return (outcome.pdf_path, *outcome.auxiliary_paths)
            case WorkflowStage.QUARANTINE:
                return None
            case WorkflowStage.ROUTE:
                raise InvalidExecutionStageError(stage)
            case unreachable:
                assert_never(unreachable)
