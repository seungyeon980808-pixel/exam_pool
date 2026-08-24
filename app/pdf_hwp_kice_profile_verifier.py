"""Profile adapter for generated-PDF KICE figure structure evidence."""
from __future__ import annotations

from dataclasses import dataclass

import fitz

from .pdf_hwp_kice_structural import (
    FigurePlacement,
    KiceFigureExpectation,
    KiceStructuralRequest,
    PreparedVisualKind,
    inspect_kice_figure_structure,
)
from .pdf_hwp_kice_structural_geometry import classify_figure_placement
from .pdf_hwp_kice_source_placement import resolve_source_bbox
from .pdf_hwp_pipeline_models import DetectedItem
from .pdf_hwp_roundtrip_profile import ImageOwnershipResult, ObservedProfileIssue
from .pdf_hwp_roundtrip_unit_store import PreparedUnitRecord


def _visual_kind(record: PreparedUnitRecord) -> PreparedVisualKind:
    return (
        PreparedVisualKind.TABLE
        if any("표" in material.slot_name for material in record.structure.materials)
        else PreparedVisualKind.FIGURE
    )


def _placement(record: PreparedUnitRecord) -> FigurePlacement:
    assets = record.unit.figure_assets
    if not assets:
        return FigurePlacement.NONE
    source_pdf = assets[0].metadata.source_pdf
    if not source_pdf.is_file():
        return FigurePlacement.BETWEEN_STEM_AND_ASK
    item = DetectedItem(
        record.structure.source_page, record.structure.number, 0,
        record.structure.item_bbox, record.structure.stem + " " + record.structure.ask,
    )
    source_bboxes = tuple(
        bbox for asset in assets
        if (bbox := resolve_source_bbox(
            asset.image_path.with_suffix(".json"),
            asset.metadata.image_bbox,
            asset.metadata.page_number,
            asset.metadata.item_number,
            record.structure.item_bbox,
        )) is not None
    )
    if len(source_bboxes) != len(assets):
        return FigurePlacement.BETWEEN_STEM_AND_ASK
    with fitz.open(source_pdf) as document:
        placement = classify_figure_placement(
            document[item.page_number - 1], item, source_bboxes,
        )
    return placement or FigurePlacement.BETWEEN_STEM_AND_ASK


@dataclass(frozen=True, slots=True)
class KiceStructuralImageOwnershipVerifier:
    """Adapt isolated KICE observations to blocking profile issues."""

    generated_pdf: Path
    generated_items: tuple[DetectedItem, ...]
    minimum_scale: float = 0.70

    def verify(self, records: tuple[PreparedUnitRecord, ...]) -> ImageOwnershipResult:
        metadata = ImageOwnershipResult.from_records(records)
        expectations = tuple(KiceFigureExpectation(
            record.structure.number,
            record.unit.figure_assets,
            _visual_kind(record),
            _placement(record),
        ) for record in records)
        observed = inspect_kice_figure_structure(KiceStructuralRequest(
            self.generated_pdf,
            self.generated_items,
            expectations,
            self.minimum_scale,
        ))
        issues = (*metadata.issues, *(ObservedProfileIssue(
            issue.code.value, issue.item_number, issue.detail,
        ) for issue in observed.issues))
        return ImageOwnershipResult(
            not issues, tuple(issues), "kice_structural_generated_pdf",
        )


__all__ = ["KiceStructuralImageOwnershipVerifier"]
