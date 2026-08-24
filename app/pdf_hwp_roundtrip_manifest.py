"""Pinned input boundary for the approved PDF-to-HWP first run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BBox = tuple[float, float, float, float]
SUBJECTS = frozenset({"p1", "p2", "c1", "c2", "b1", "b2", "e1", "e2"})
KICE_IDS = frozenset({
    "p1_2019_11", "p2_2026_11", "c1_2024_06", "c2_2023_06",
    "b1_2024_09", "b2_2022_06", "e1_2024_06", "e2_2023_11",
    "p2_2013_11", "e2_2025_09",
})
EBS_ITEMS = (
    1, 12, 24, 35, 37, 48, 60, 72, 84, 96, 108, 120, 132, 144, 156,
    168, 180, 192, 204, 216, 228, 234, 235, 237, 238, 246, 254, 262, 270, 278,
)
REGRESSION_IDS = frozenset({
    "ebs_q35_crop", "ebs_q37_crop", "ebs_q234_hapdap", "ebs_q235_hapdap",
    "ebs_q237_hapdap", "ebs_q238_hapdap", "e2_2024_09_q1_clipping",
})


class ManifestSelectionError(ValueError):
    """The manifest differs from the approved, pinned selection."""


class SourceKind(StrEnum):
    KICE_EXAM = "KICE_EXAM"
    EBS_TEXTBOOK = "EBS_TEXTBOOK"
    RASTER_SCAN = "RASTER_SCAN"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Exclusions(FrozenModel):
    overnight: Literal[52]
    heldout: Literal[8]
    intersection: Literal[0]
    union: Literal[60]
    selected_overlap: Literal[0]


class KicePaper(FrozenModel):
    paper_id: str
    subject: str
    source_kind: Literal[SourceKind.KICE_EXAM]
    path: Path
    sha256: Sha256
    expected_pages: PositiveInt
    expected_items: PositiveInt
    sample_role: Literal["stratum", "older", "newer"]


class EbsSample(FrozenModel):
    item: PositiveInt
    page: PositiveInt


class EbsSource(FrozenModel):
    source_id: Literal["ebs_2027_physics1"]
    source_kind: Literal[SourceKind.EBS_TEXTBOOK]
    path: Path
    sha256: Sha256
    expected_pages: Literal[248]
    expected_items: Literal[278]
    sample: tuple[EbsSample, ...]


class RegressionCase(FrozenModel):
    case_id: str
    source_id: str
    item: PositiveInt
    page: PositiveInt
    item_bbox: BBox
    crop_bbox: BBox | None = None
    expected: Literal[
        "crop_contamination", "crop_clipping", "hapdap_template_with_three_bogi_slots"
    ]
    source_kind: Literal[SourceKind.KICE_EXAM] | None = None
    path: Path | None = None
    sha256: Sha256 | None = None

    @model_validator(mode="after")
    def validate_external_source(self) -> "RegressionCase":
        supplied = (self.source_kind is not None, self.path is not None, self.sha256 is not None)
        if any(supplied) and not all(supplied):
            raise ManifestSelectionError("external regression source requires kind, path, and sha256")
        return self


class RasterFixture(FrozenModel):
    source_id: Literal["item20_original"]
    source_kind: Literal[SourceKind.RASTER_SCAN]
    path: Path
    sha256: Sha256
    width: Literal[1718]
    height: Literal[555]
    expected_pages: Literal[1]
    expected_items: Literal[1]
    companion_pdf_path: Path | None = None
    companion_pdf_sha256: Sha256 | None = None
    companion_item: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_companion(self) -> "RasterFixture":
        supplied = (
            self.companion_pdf_path is not None,
            self.companion_pdf_sha256 is not None,
            self.companion_item is not None,
        )
        if any(supplied) and not all(supplied):
            raise ManifestSelectionError(
                "raster companion requires path, sha256, and item",
            )
        return self


class ApprovedFirstRunManifest(FrozenModel):
    schema_version: Literal[1]
    run_id: Literal["approved-first-run"]
    exclusions: Exclusions
    kice_papers: tuple[KicePaper, ...]
    ebs_source: EbsSource
    fixed_regressions: tuple[RegressionCase, ...]
    raster_fixture: RasterFixture

    @model_validator(mode="after")
    def validate_selection(self) -> "ApprovedFirstRunManifest":
        ids = [paper.paper_id for paper in self.kice_papers]
        if len(ids) != 10 or frozenset(ids) != KICE_IDS:
            raise ManifestSelectionError("approved run requires the pinned 10 KICE papers")
        if {paper.subject for paper in self.kice_papers} != SUBJECTS:
            raise ManifestSelectionError("KICE papers must cover all eight subject strata")
        paths = [paper.path for paper in self.kice_papers]
        if len(paths) != len(set(paths)):
            raise ManifestSelectionError("duplicate KICE source paths")
        items = tuple(sample.item for sample in self.ebs_source.sample)
        if len(items) != len(set(items)):
            raise ManifestSelectionError("duplicate EBS sample items")
        if items != EBS_ITEMS:
            raise ManifestSelectionError("approved run requires the pinned 30 EBS items")
        regressions = [case.case_id for case in self.fixed_regressions]
        if len(regressions) != 7 or frozenset(regressions) != REGRESSION_IDS:
            raise ManifestSelectionError("approved run requires the pinned seven regressions")
        return self


@dataclass(frozen=True, slots=True)
class SourceCheck:
    source_id: str
    path: Path
    exists: bool
    hash_matches: bool | None
    page_count_matches: bool | None = None
    item_count_matches: bool | None = None

    @property
    def ok(self) -> bool:
        return (
            self.exists
            and self.hash_matches is True
            and self.page_count_matches is not False
            and self.item_count_matches is not False
        )


@dataclass(frozen=True, slots=True)
class ManifestVerification:
    checks: tuple[SourceCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass(frozen=True, slots=True)
class _Expectation:
    source_id: str
    path: Path
    sha256: str
    pages: int | None = None
    items: int | None = None


def load_manifest(path: Path) -> ApprovedFirstRunManifest:
    """Parse and validate an approved-first-run manifest."""
    return ApprovedFirstRunManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _expectations(manifest: ApprovedFirstRunManifest) -> tuple[_Expectation, ...]:
    primary = [
        _Expectation(p.paper_id, p.path, p.sha256, p.expected_pages, p.expected_items)
        for p in manifest.kice_papers
    ]
    ebs = manifest.ebs_source
    primary.append(_Expectation(ebs.source_id, ebs.path, ebs.sha256, ebs.expected_pages, ebs.expected_items))
    for case in manifest.fixed_regressions:
        if case.path is not None and case.sha256 is not None:
            primary.append(_Expectation(case.source_id, case.path, case.sha256))
    raster = manifest.raster_fixture
    primary.append(_Expectation(raster.source_id, raster.path, raster.sha256))
    if raster.companion_pdf_path is not None and raster.companion_pdf_sha256 is not None:
        primary.append(_Expectation(
            f"{raster.source_id}:companion",
            raster.companion_pdf_path,
            raster.companion_pdf_sha256,
        ))
    return tuple(primary)


def verify_manifest_sources(
    manifest: ApprovedFirstRunManifest, *, check_expectations: bool = False
) -> ManifestVerification:
    """Verify source existence/hash and optionally PDF page/item expectations."""
    from app.pdf_hwp_pipeline import detect_items

    checks: list[SourceCheck] = []
    for expected in _expectations(manifest):
        exists = expected.path.is_file()
        hash_matches = _sha256(expected.path) == expected.sha256 if exists else None
        page_matches: bool | None = None
        item_matches: bool | None = None
        if exists and check_expectations and expected.pages is not None:
            detection = detect_items(expected.path)
            page_matches = detection.page_count == expected.pages
            item_matches = len(detection.items) == expected.items
        checks.append(SourceCheck(
            expected.source_id, expected.path, exists, hash_matches, page_matches, item_matches
        ))
    return ManifestVerification(tuple(checks))
