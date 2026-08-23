"""Geometry gates for unsafe PDF figure crops in round-trip QA."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import re
from typing import Annotated, Final

import fitz
from pydantic import AfterValidator, BaseModel, ConfigDict, Field


RawBBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class InvalidBBoxError(ValueError):
    """Raised when a geometry boundary receives an empty rectangle."""

    bbox: RawBBox

    def __str__(self) -> str:
        return f"bbox must have positive width and height: {self.bbox}"


def _ordered_bbox(value: RawBBox) -> RawBBox:
    if value[2] <= value[0] or value[3] <= value[1]:
        raise InvalidBBoxError(value)
    return value


BBox = Annotated[RawBBox, AfterValidator(_ordered_bbox)]
_BOUNDARY_TOLERANCE: Final = 0.5
_PROSE_CHARACTER_MINIMUM: Final = 24
_PROSE_WORD_MINIMUM: Final = 8
_PROSE_WIDTH_RATIO: Final = 0.35
_PROSE_OVERLAP_RATIO: Final = 0.6
_IMAGE_OVERLAP_RATIO: Final = 0.2


class CropAuditIssue(StrEnum):
    """Stable failure codes consumed by resumable round-trip reports."""

    CROP_CONTAMINATION = "crop_contamination"
    CROP_CLIPPING = "crop_clipping"
    SOURCE_BOUNDARY_SPILL = "source_boundary_spill"


class TextGeometry(BaseModel):
    """One PDF text block reduced to geometry and prose-density evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bbox: BBox
    character_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    text: str = ""


class CropGeometry(BaseModel):
    """Trusted geometry snapshot for one proposed final figure crop."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    item_number: int = Field(ge=1)
    item_bbox: BBox
    crop_bbox: BBox
    editable_text: str = ""
    semantic_selection: bool = False
    text_regions: tuple[TextGeometry, ...] = ()
    image_bboxes: tuple[BBox, ...] = ()


@dataclass(frozen=True, slots=True)
class CropSourceRequest:
    """PDF boundary input used to reproduce a geometry snapshot."""

    source_pdf: Path
    page_number: int
    item_number: int
    item_bbox: RawBBox
    crop_bbox: RawBBox
    editable_text: str = ""
    semantic_selection: bool = False


@dataclass(frozen=True, slots=True)
class CropAuditResult:
    """Machine codes and exact geometry that caused quarantine."""

    issues: tuple[CropAuditIssue, ...]
    contaminated_text_bboxes: tuple[RawBBox, ...]
    clipped_image_bboxes: tuple[RawBBox, ...]


def _area(box: RawBBox) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _intersection_area(left: RawBBox, right: RawBBox) -> float:
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    return max(0.0, width) * max(0.0, height)


def _contains(outer: RawBBox, inner: RawBBox, tolerance: float = 0.0) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def _duplicates_editable_text(region: TextGeometry, editable_text: str) -> bool:
    if not editable_text or not region.text:
        return True
    region_tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", region.text.casefold())
    editable_tokens = set(re.findall(
        r"[0-9A-Za-z가-힣]{2,}", editable_text.casefold(),
    ))
    return bool(region_tokens) and sum(
        token in editable_tokens for token in region_tokens
    ) / len(region_tokens) >= _PROSE_OVERLAP_RATIO


def audit_crop_geometry(geometry: CropGeometry) -> CropAuditResult:
    """Reject prose contamination, clipped images, and item-boundary spill."""
    item_width = geometry.item_bbox[2] - geometry.item_bbox[0]
    contaminated = tuple(
        region.bbox
        for region in geometry.text_regions
        if region.character_count >= _PROSE_CHARACTER_MINIMUM
        and region.word_count >= _PROSE_WORD_MINIMUM
        and region.bbox[2] - region.bbox[0] >= item_width * _PROSE_WIDTH_RATIO
        and _intersection_area(region.bbox, geometry.crop_bbox) / _area(region.bbox)
        >= _PROSE_OVERLAP_RATIO
        and _duplicates_editable_text(region, geometry.editable_text)
    )
    clipped = tuple(
        image_bbox
        for image_bbox in geometry.image_bboxes
        if not geometry.semantic_selection
        and _intersection_area(image_bbox, geometry.crop_bbox) / _area(image_bbox)
        >= _IMAGE_OVERLAP_RATIO
        and not _contains(geometry.crop_bbox, image_bbox, _BOUNDARY_TOLERANCE)
    )
    issues: list[CropAuditIssue] = []
    if contaminated:
        issues.append(CropAuditIssue.CROP_CONTAMINATION)
    if clipped:
        issues.append(CropAuditIssue.CROP_CLIPPING)
    if not _contains(geometry.item_bbox, geometry.crop_bbox, _BOUNDARY_TOLERANCE):
        issues.append(CropAuditIssue.SOURCE_BOUNDARY_SPILL)
    return CropAuditResult(tuple(issues), contaminated, clipped)


def read_crop_geometry(request: CropSourceRequest) -> CropGeometry:
    """Read decisive text and image rectangles from the real source PDF."""
    crop = fitz.Rect(request.crop_bbox)
    with request.source_pdf.open("rb") as source_stream:
        source_hash = hashlib.file_digest(source_stream, "sha256").hexdigest()
    with fitz.open(request.source_pdf) as document:
        page = document[request.page_number - 1]
        text_regions = tuple(
            TextGeometry(
                bbox=tuple(float(value) for value in block[:4]),
                character_count=len("".join(str(block[4]).split())),
                word_count=len(str(block[4]).split()),
                text=str(block[4]),
            )
            for block in page.get_text("blocks")
            if not (fitz.Rect(block[:4]) & crop).is_empty
        )
        image_bboxes = tuple(
            tuple(float(value) for value in image["bbox"])
            for image in page.get_image_info(xrefs=True)
            if not (fitz.Rect(image["bbox"]) & crop).is_empty
        )
    return CropGeometry(
        source_sha256=source_hash,
        page_number=request.page_number,
        item_number=request.item_number,
        item_bbox=request.item_bbox,
        crop_bbox=request.crop_bbox,
        editable_text=request.editable_text,
        semantic_selection=request.semantic_selection,
        text_regions=text_regions,
        image_bboxes=image_bboxes,
    )
