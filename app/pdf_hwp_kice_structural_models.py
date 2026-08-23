"""Typed public contract for generated-PDF KICE figure verification."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .pdf_hwp_pipeline_models import DetectedItem, FigureAsset


class PreparedVisualKind(StrEnum):
    FIGURE = "figure"
    TABLE = "table"


class FigurePlacement(StrEnum):
    NONE = "none"
    BEFORE_ASK = "before_ask"
    BETWEEN_STEM_AND_ASK = "between_stem_and_ask"
    SIDE_BY_SIDE = "side_by_side"
    AFTER_ASK = "after_ask"


class KiceFigureIssue(StrEnum):
    UNREADABLE_PDF = "kice_structural_unreadable_pdf"
    FIGURE_MISSING = "kice_structural_figure_missing"
    FIGURE_OWNER_AMBIGUOUS = "kice_structural_figure_owner_ambiguous"
    FIGURE_OWNER_MISMATCH = "kice_structural_figure_owner_mismatch"
    PANEL_COUNT_MISMATCH = "kice_structural_panel_count_mismatch"
    PANEL_ORDER_MISMATCH = "kice_structural_panel_order_mismatch"
    PLACEMENT_MISMATCH = "kice_structural_placement_mismatch"
    SCALE_UNREADABLE = "kice_structural_scale_unreadable"
    CROSS_ITEM_SPILL = "kice_structural_cross_item_spill"
    SOURCE_CROP_CONTAMINATION = "kice_structural_source_crop_contamination"


class KiceFigureDiagnostic(StrEnum):
    VECTOR_OR_TABLE_UNOBSERVED = "kice_structural_vector_or_table_unobserved"
    IMAGE_MATCH_UNOBSERVED = "kice_structural_image_match_unobserved"
    PLACEMENT_UNOBSERVED = "kice_structural_placement_unobserved"
    SOURCE_CROP_UNOBSERVED = "kice_structural_source_crop_unobserved"


@dataclass(frozen=True, slots=True)
class KiceFigureExpectation:
    item_number: int
    assets: tuple[FigureAsset, ...]
    visual_kind: PreparedVisualKind = PreparedVisualKind.FIGURE
    placement: FigurePlacement = FigurePlacement.BETWEEN_STEM_AND_ASK


@dataclass(frozen=True, slots=True)
class KiceStructuralRequest:
    generated_pdf: Path
    generated_items: tuple[DetectedItem, ...]
    expectations: tuple[KiceFigureExpectation, ...]
    minimum_scale: float = 0.70


@dataclass(frozen=True, slots=True)
class KiceStructuralIssue:
    code: KiceFigureIssue
    item_number: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class KiceStructuralDiagnostic:
    code: KiceFigureDiagnostic
    item_number: int
    detail: str


@dataclass(frozen=True, slots=True)
class KiceStructuralItem:
    item_number: int
    expected_count: int
    observed_count: int
    placement: FigurePlacement | None
    minimum_scale: float | None


@dataclass(frozen=True, slots=True)
class KiceStructuralResult:
    generated_pdf: Path
    items: tuple[KiceStructuralItem, ...]
    issues: tuple[KiceStructuralIssue, ...]
    diagnostics: tuple[KiceStructuralDiagnostic, ...]
