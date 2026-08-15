"""Typed domain contracts for the DB-agnostic PDF-to-HWP pipeline."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


BoundingBox: TypeAlias = tuple[float, float, float, float]


class LayoutStyle(StrEnum):
    SCHOOL = "school"
    SUNEUNG = "suneung"


class PipelinePhase(StrEnum):
    PREPARING = "preparing"
    TYPESETTING = "typesetting"
    COMPLETE = "complete"


class PanelMode(StrEnum):
    SINGLE = "single"
    SEPARATE = "separate"
    COMPOSITE = "composite"


class SourceKind(StrEnum):
    RASTER = "raster"
    VECTOR = "vector"
    MIXED = "mixed"


class FigureArrangement(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    GRID = "grid"
    COMPOSITE = "composite"


class DisplaySize(StrEnum):
    SMALL = "small"
    LARGE = "large"


class FigureLayout(StrEnum):
    ONE_SMALL = "one_small"
    ONE_LARGE = "one_large"
    TWO_SMALL = "two_small"
    TWO_LARGE = "two_large"
    TWO_VERTICAL = "two_vertical"
    THREE_SMALL = "three_small"


@dataclass(frozen=True, slots=True)
class FigureLayoutMetadata:
    layout: FigureLayout
    candidate_layout: FigureLayout
    asset_count: int
    panel_width_points: tuple[float, ...]
    combined_width_points: float
    arrangement: FigureArrangement
    small_pair_readable: bool
    template_usable_size_mm: tuple[float, float | None] | None
    projected_scale_factors: tuple[float, ...]
    minimum_projected_scale: float | None
    candidate_minimum_projected_scale: float | None
    readability_threshold: float


class FigureAssetMetadata(BaseModel):
    """Validated captionless figure metadata shared by preview and HWP."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    source_pdf: Path
    page_number: int = Field(ge=1)
    item_number: int = Field(ge=1)
    image_bbox: BoundingBox
    caption_text: str
    caption_bbox: BoundingBox | None
    asset_count: int = Field(ge=1, le=3)
    panel_index: int = Field(ge=1, le=3)
    panel_mode: PanelMode
    arrangement: FigureArrangement
    source_kind: SourceKind = SourceKind.RASTER
    display_size: DisplaySize
    dpi: int = Field(gt=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0.0, le=1.0)
    caption_in_image: Literal[False] = False
    manual_review_required: bool = False
    review_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def panel_index_belongs_to_asset_set(self) -> FigureAssetMetadata:
        if self.panel_index > self.asset_count:
            raise PydanticCustomError(
                "panel_index_out_of_range",
                "panel_index must not exceed asset_count",
            )
        return self


@dataclass(frozen=True, slots=True)
class FigureAsset:
    image_path: Path
    metadata: FigureAssetMetadata


class GraphicalChoiceAssetMetadata(BaseModel):
    """Validated identity and order for one graphical answer choice."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    source_pdf: Path
    page_number: int = Field(ge=1)
    item_number: int = Field(ge=1)
    choice_index: int = Field(ge=1, le=5)
    asset_count: Literal[5] = 5
    dpi: int = Field(gt=0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0.0, le=1.0)
    manual_review_required: bool = False
    review_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphicalChoiceAsset:
    image_path: Path
    metadata: GraphicalChoiceAssetMetadata


@dataclass(frozen=True, slots=True)
class DetectedItem:
    page_number: int
    item_number: int
    column: int
    bbox: tuple[float, float, float, float]
    source_text: str


@dataclass(frozen=True, slots=True)
class DetectionResult:
    source_pdf: Path
    source_hash: str
    page_count: int
    items: tuple[DetectedItem, ...]


@dataclass(frozen=True, slots=True)
class CropArtifact:
    image_path: Path
    provenance_path: Path
    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class DraftArtifact:
    item_number: int
    palette_markdown: str
    source_text: str
    choice_texts: tuple[str, ...]
    source_image: CropArtifact
    figure_asset: CropArtifact | None
    warnings: tuple[str, ...]
    figure_assets: tuple[CropArtifact, ...] = ()
    graphical_choice_assets: tuple[CropArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class ConversionUnit:
    item_number: int
    palette_markdown: str
    figure_assets: tuple[FigureAsset, ...] = ()
    graphical_choice_assets: tuple[GraphicalChoiceAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class ConversionRequest:
    job_key: str
    units: tuple[ConversionUnit, ...]
    output_dir: Path
    layout_style: LayoutStyle
    asset_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedDocument:
    hwp_path: Path
    pdf_path: Path
    rendered_pages: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ConversionResult:
    hwp_path: Path
    pdf_path: Path
    rendered_pages: tuple[Path, ...]
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class PipelineProgress:
    phase: PipelinePhase
    completed_items: int
    total_items: int


@dataclass(frozen=True, slots=True)
class InvalidSourcePdfError(RuntimeError):
    source_pdf: Path
    detail: str

    def __str__(self) -> str:
        return f"invalid source PDF {self.source_pdf}: {self.detail}"


@dataclass(frozen=True, slots=True)
class InvalidCropError(RuntimeError):
    item: DetectedItem

    def __str__(self) -> str:
        return f"invalid crop for page {self.item.page_number}, item {self.item.item_number}"


@dataclass(frozen=True, slots=True)
class ConversionTypesetError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return f"HwpPalette conversion failed: {self.detail}"


@dataclass(frozen=True, slots=True)
class ConversionResourceLockedError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return f"HwpPalette resource is locked: {self.detail}"


@dataclass(frozen=True, slots=True)
class EmptyConversionError(RuntimeError):
    job_key: str

    def __str__(self) -> str:
        return f"conversion job {self.job_key!r} has no selected items"


@dataclass(frozen=True, slots=True)
class DraftExtractionError(RuntimeError):
    page_number: int
    item_number: int
    detail: str

    def __str__(self) -> str:
        return f"draft extraction failed for page {self.page_number}, item {self.item_number}: {self.detail}"


@dataclass(frozen=True, slots=True)
class UnsupportedDraftLayoutError(RuntimeError):
    page_number: int
    item_number: int
    detail: str
    source_image: CropArtifact

    def __str__(self) -> str:
        return f"unsupported draft layout for page {self.page_number}, item {self.item_number}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ManualReviewRequiredError(RuntimeError):
    item_number: int
    detail: str

    def __str__(self) -> str:
        return f"item {self.item_number} requires manual review: {self.detail}"


class DocumentTypesetter(Protocol):
    def typeset(
        self,
        markdown: str,
        output_dir: Path,
        layout_style: LayoutStyle,
        asset_dirs: tuple[Path, ...],
    ) -> GeneratedDocument: ...


ProgressSink = Callable[[PipelineProgress], None]
