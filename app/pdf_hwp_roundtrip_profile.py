"""Frozen source-profile acceptance evidence and severity policy."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
from typing import Final, Literal, Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from .pdf_hwp_atomic import atomic_replace
from .pdf_hwp_roundtrip_models import SourceProfile
from .pdf_hwp_roundtrip_unit_store import ItemFailure, PreparedUnitRecord


class IssueSeverity(StrEnum):
    BLOCKING = "blocking"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, slots=True)
class ObservedProfileIssue:
    code: str
    item_number: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ProfileIssue:
    code: str
    severity: IssueSeverity
    item_number: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class VisualMetric:
    item_number: int
    pixel_mae: float
    edge_mae: float


@dataclass(frozen=True, slots=True)
class FieldCoverage:
    field: str
    present_count: int
    expected_count: int


@dataclass(frozen=True, slots=True)
class StructuralCoverage:
    total_count: int
    complete_count: int
    fields: tuple[FieldCoverage, ...]


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    passed: bool
    issues: tuple[ObservedProfileIssue, ...]


@dataclass(frozen=True, slots=True)
class ImageOwnershipResult:
    passed: bool
    issues: tuple[ObservedProfileIssue, ...]
    verifier: str = "prepared_asset_metadata"

    @classmethod
    def from_records(cls, records: tuple[PreparedUnitRecord, ...]) -> ImageOwnershipResult:
        """Default metadata hook; an isolated image verifier may replace this result."""
        issues = tuple(
            ObservedProfileIssue(
                "wrong_image_ownership", record.structure.number,
                f"asset {ref.asset_path} owner {ref.owner_item_number} != item {record.structure.number}",
            )
            for record in records for ref in record.structure.asset_refs
            if ref.owner_item_number != record.structure.number
        )
        return cls(not issues, issues)


class ImageOwnershipVerifier(Protocol):
    """Hook implemented by the isolated figure verifier when it becomes available."""

    def verify(self, records: tuple[PreparedUnitRecord, ...]) -> ImageOwnershipResult: ...


@dataclass(frozen=True, slots=True)
class MetadataImageOwnershipVerifier:
    def verify(self, records: tuple[PreparedUnitRecord, ...]) -> ImageOwnershipResult:
        return ImageOwnershipResult.from_records(records)


@dataclass(frozen=True, slots=True)
class ProfileVerificationRequest:
    profile: SourceProfile
    records: tuple[PreparedUnitRecord, ...]
    preparation_failures: tuple[ItemFailure, ...]
    generated_count: int
    editability_issues: tuple[ObservedProfileIssue, ...]
    pdf_issues: tuple[ObservedProfileIssue, ...]
    semantic_issues: tuple[ObservedProfileIssue, ...]
    alignment_issues: tuple[ObservedProfileIssue, ...]
    visual_metrics: tuple[VisualMetric, ...]
    image_ownership: ImageOwnershipResult
    hwp_issues: tuple[ObservedProfileIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileVerificationRecord:
    schema_version: Literal[1]
    profile: SourceProfile
    prepared_count: int
    generated_count: int
    structural_coverage: StructuralCoverage
    editability: VerificationCheck
    hwp: VerificationCheck
    pdf_semantic: VerificationCheck
    image_ownership: ImageOwnershipResult
    blocking_issues: tuple[ProfileIssue, ...]
    diagnostics: tuple[ProfileIssue, ...]


_FIELD_ORDER: Final = ("number", "stem", "materials", "ask", "bogi", "choices")
_DIAGNOSTIC_CODES: Final = frozenset({
    "visual_mismatch", "visual_metric", "small_geometry_delta",
    "small_geometry_mismatch", "geometry_delta",
})
_RECORD_ADAPTER: Final = TypeAdapter(ProfileVerificationRecord)


def _coverage(request: ProfileVerificationRequest) -> StructuralCoverage:
    records = request.records
    total = len(records) + len(request.preparation_failures)
    counts = {
        "number": sum(row.structure.number > 0 for row in records),
        "stem": sum(bool(row.structure.stem.strip()) for row in records),
        "materials": sum(bool(row.structure.materials) for row in records),
        "ask": sum(bool(row.structure.ask.strip()) for row in records),
        "bogi": sum(bool(row.structure.bogi) for row in records),
        "choices": sum(len(row.structure.choices) == 5 for row in records),
    }
    fields = tuple(FieldCoverage(name, counts[name], len(records)) for name in _FIELD_ORDER)
    return StructuralCoverage(total, len(records), fields)


def _profile_issue(issue: ObservedProfileIssue) -> ProfileIssue:
    severity = (
        IssueSeverity.DIAGNOSTIC if issue.code in _DIAGNOSTIC_CODES
        else IssueSeverity.BLOCKING
    )
    return ProfileIssue(issue.code, severity, issue.item_number, issue.detail)


def classify_profile(request: ProfileVerificationRequest) -> ProfileVerificationRecord:
    """Apply the closed profile policy without conflating visual similarity with structure."""
    coverage = _coverage(request)
    observed = [
        *(ObservedProfileIssue(failure.code.value, failure.item_number, failure.detail)
          for failure in request.preparation_failures),
        *request.editability_issues, *request.hwp_issues, *request.pdf_issues,
        *request.semantic_issues, *request.alignment_issues,
        *request.image_ownership.issues,
    ]
    if request.generated_count != len(request.records):
        observed.append(ObservedProfileIssue(
            "generated_item_count_mismatch", None,
            f"generated {request.generated_count} != prepared {len(request.records)}",
        ))
    if request.profile is SourceProfile.KICE_STRUCTURAL and coverage.complete_count != coverage.total_count:
        observed.append(ObservedProfileIssue(
            "typed_structure_coverage_incomplete", None,
            f"typed structures {coverage.complete_count}/{coverage.total_count}",
        ))
    observed.extend(ObservedProfileIssue(
        "visual_metric", metric.item_number,
        f"pixel_mae={metric.pixel_mae:.6f},edge_mae={metric.edge_mae:.6f}",
    ) for metric in request.visual_metrics)
    classified = tuple(_profile_issue(issue) for issue in observed)
    blocking = tuple(issue for issue in classified if issue.severity is IssueSeverity.BLOCKING)
    diagnostics = tuple(issue for issue in classified if issue.severity is IssueSeverity.DIAGNOSTIC)
    return ProfileVerificationRecord(
        1, request.profile, len(request.records), request.generated_count, coverage,
        VerificationCheck(not request.editability_issues, request.editability_issues),
        VerificationCheck(not request.hwp_issues, request.hwp_issues),
        VerificationCheck(not (request.pdf_issues or request.semantic_issues),
                          (*request.pdf_issues, *request.semantic_issues)),
        request.image_ownership, blocking, diagnostics,
    )


def write_profile_verification(path: Path, record: ProfileVerificationRecord) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    content = _RECORD_ADAPTER.dump_json(record, indent=2).decode() + "\n"
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_replace(temporary, target)
    return target


def load_profile_verification(path: Path) -> ProfileVerificationRecord:
    return _RECORD_ADAPTER.validate_json(path.resolve().read_bytes())


__all__ = [
    "ImageOwnershipResult", "ImageOwnershipVerifier", "IssueSeverity",
    "MetadataImageOwnershipVerifier", "ObservedProfileIssue", "ProfileIssue",
    "ProfileVerificationRecord", "ProfileVerificationRequest", "VisualMetric",
    "classify_profile", "load_profile_verification", "write_profile_verification",
]
