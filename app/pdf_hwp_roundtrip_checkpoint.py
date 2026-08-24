"""Atomic hash-keyed persistence and deterministic checkpoint resumption."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Final, assert_never
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from .pdf_hwp_atomic import atomic_replace
from .pdf_hwp_roundtrip_models import (
    ArtifactCheckpoint,
    ArtifactHash,
    EbsRoute,
    KiceRoute,
    PersistedStageArtifact,
    QuarantineReason,
    RasterRoute,
    SourceKind,
    SourceRoute,
    UnknownRoute,
    WorkflowStage,
)
from .pdf_hwp_roundtrip_router import scheduled_stages


_HASH_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class _ArtifactPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: WorkflowStage
    path: Path
    artifact_hash: str


class _CheckpointPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_hash: str
    source_kind: SourceKind
    quarantine_reason: QuarantineReason | None = None
    completed_stages: tuple[WorkflowStage, ...]
    artifacts: tuple[_ArtifactPayload, ...] = ()


@dataclass(frozen=True, slots=True)
class InvalidArtifactHashError(ValueError):
    artifact_hash: str

    def __str__(self) -> str:
        return f"artifact hash is not a lowercase SHA-256 digest: {self.artifact_hash!r}"


@dataclass(frozen=True, slots=True)
class CorruptCheckpointError(ValueError):
    path: Path
    detail: str

    def __str__(self) -> str:
        return f"checkpoint {self.path} is corrupt: {self.detail}"


def artifact_hash(path: Path) -> ArtifactHash:
    """Hash the exact artifact bytes used as the checkpoint identity."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return ArtifactHash(digest.hexdigest())


def _payload_route(payload: _CheckpointPayload, path: Path) -> SourceRoute:
    match payload.source_kind:
        case SourceKind.KICE:
            route: SourceRoute = KiceRoute()
        case SourceKind.EBS:
            route = EbsRoute()
        case SourceKind.RASTER:
            route = RasterRoute()
        case SourceKind.UNKNOWN:
            if payload.quarantine_reason is None:
                raise CorruptCheckpointError(path, "UNKNOWN route requires quarantine_reason")
            route = UnknownRoute(payload.quarantine_reason)
        case unreachable:
            assert_never(unreachable)
    if route.kind is not SourceKind.UNKNOWN and payload.quarantine_reason is not None:
        raise CorruptCheckpointError(path, "known route cannot carry quarantine_reason")
    return route


def _route_reason(route: SourceRoute) -> QuarantineReason | None:
    match route:
        case KiceRoute() | EbsRoute() | RasterRoute():
            return None
        case UnknownRoute(reason=reason):
            return reason
        case unreachable:
            assert_never(unreachable)


class CheckpointStore:
    """Persist complete checkpoints with same-directory atomic replacement."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, digest: ArtifactHash) -> Path:
        if _HASH_PATTERN.fullmatch(digest) is None:
            raise InvalidArtifactHashError(digest)
        return self._root / f"{digest}.json"

    def save(self, checkpoint: ArtifactCheckpoint) -> Path:
        target = self._path(checkpoint.artifact_hash)
        self._root.mkdir(parents=True, exist_ok=True)
        payload = _CheckpointPayload(
            artifact_hash=checkpoint.artifact_hash,
            source_kind=checkpoint.route.kind,
            quarantine_reason=_route_reason(checkpoint.route),
            completed_stages=checkpoint.completed_stages,
            artifacts=tuple(
                _ArtifactPayload(
                    stage=artifact.stage,
                    path=artifact.path,
                    artifact_hash=artifact.artifact_hash,
                )
                for artifact in checkpoint.artifacts
            ),
        )
        temporary = self._root / f".{checkpoint.artifact_hash}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(payload.model_dump_json(indent=2))
                destination.write("\n")
                destination.flush()
                os.fsync(destination.fileno())
            atomic_replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def load(self, digest: ArtifactHash) -> ArtifactCheckpoint | None:
        target = self._path(digest)
        if not target.is_file():
            return None
        try:
            payload = _CheckpointPayload.model_validate_json(target.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise CorruptCheckpointError(target, str(exc)) from exc
        if payload.artifact_hash != digest:
            raise CorruptCheckpointError(target, "payload hash does not match filename key")
        return ArtifactCheckpoint(
            artifact_hash=ArtifactHash(payload.artifact_hash),
            route=_payload_route(payload, target),
            completed_stages=payload.completed_stages,
            artifacts=tuple(
                PersistedStageArtifact(
                    artifact.stage, artifact.path, ArtifactHash(artifact.artifact_hash),
                )
                for artifact in payload.artifacts
            ),
        )


def next_pending_stage(checkpoint: ArtifactCheckpoint) -> WorkflowStage | None:
    """Resume at the first durable stage absent from the checkpoint."""
    plan = (WorkflowStage.ROUTE, *scheduled_stages(checkpoint.route))
    return next((stage for stage in plan if stage not in checkpoint.completed_stages), None)
