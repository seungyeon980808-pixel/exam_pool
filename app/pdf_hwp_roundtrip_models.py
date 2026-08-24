"""Typed state models for resumable PDF-to-HWP round trips."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import NewType


ArtifactHash = NewType("ArtifactHash", str)


class SourceIntegrity(str, Enum):
    """Whether the source crossed the PDF parsing boundary successfully."""

    VALID = "valid"
    MALFORMED = "malformed"


class SourceKind(str, Enum):
    """Closed source families understood by the harness."""

    KICE = "kice"
    EBS = "ebs"
    RASTER = "raster"
    UNKNOWN = "unknown"


class SourceProfile(str, Enum):
    """Closed acceptance policies for supported round-trip source families."""

    EBS_EDITABLE_REFLOW = "ebs_editable_reflow"
    KICE_STRUCTURAL = "kice_structural"


class QuarantineReason(str, Enum):
    """Machine-consumable reasons that prohibit conversion scheduling."""

    MALFORMED = "malformed"
    UNRECOGNIZED = "unrecognized"


class WorkflowStage(str, Enum):
    """Durable stages in artifact processing order."""

    ROUTE = "route"
    EXTRACT = "extract"
    HWP = "hwp"
    PDF = "pdf"
    QUARANTINE = "quarantine"


@dataclass(frozen=True, slots=True)
class InvalidSourceFactsError(ValueError):
    """Source page counts violate their typed invariant."""

    page_count: int
    raster_page_count: int

    def __str__(self) -> str:
        return (
            "source page counts require page_count >= 0 and "
            f"0 <= raster_page_count <= page_count; got {self.raster_page_count}/{self.page_count}"
        )


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """Parsed evidence used to route one source without reopening it."""

    filename: str
    identity_text: str
    source_text: str
    page_count: int
    raster_page_count: int
    integrity: SourceIntegrity

    def __post_init__(self) -> None:
        if self.page_count < 0 or not 0 <= self.raster_page_count <= self.page_count:
            raise InvalidSourceFactsError(self.page_count, self.raster_page_count)


@dataclass(frozen=True, slots=True)
class KiceRoute:
    kind: SourceKind = field(default=SourceKind.KICE, init=False)
    quarantined: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class EbsRoute:
    kind: SourceKind = field(default=SourceKind.EBS, init=False)
    quarantined: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class RasterRoute:
    kind: SourceKind = field(default=SourceKind.RASTER, init=False)
    quarantined: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class UnknownRoute:
    reason: QuarantineReason
    kind: SourceKind = field(default=SourceKind.UNKNOWN, init=False)
    quarantined: bool = field(default=True, init=False)


SourceRoute = KiceRoute | EbsRoute | RasterRoute | UnknownRoute


@dataclass(frozen=True, slots=True)
class PersistedStageArtifact:
    """Path and digest evidence produced by one completed durable stage."""

    stage: WorkflowStage
    path: Path
    artifact_hash: ArtifactHash


@dataclass(frozen=True, slots=True)
class ArtifactCheckpoint:
    """Immutable restart state keyed by the source artifact digest."""

    artifact_hash: ArtifactHash
    route: SourceRoute
    completed_stages: tuple[WorkflowStage, ...]
    artifacts: tuple[PersistedStageArtifact, ...] = ()
