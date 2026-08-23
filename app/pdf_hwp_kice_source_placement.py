"""Resolve authoritative source geometry for KICE figure placement."""
from __future__ import annotations

from pathlib import Path

import fitz
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from .pdf_hwp_kice_structural_geometry import classify_figure_placement
from .pdf_hwp_kice_structural_models import FigurePlacement
from .pdf_hwp_pipeline_models import (
    CropArtifact, DetectedItem, FigureAssetMetadata, FigureLayout,
)


class _FigureProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    page_number: int = Field(ge=1)
    item_number: int = Field(ge=1)
    source_bbox: tuple[float, float, float, float] = Field(
        validation_alias=AliasChoices("source_bbox", "bbox", "image_bbox"),
    )


def resolve_source_bbox(
    sidecar: Path,
    image_bbox: tuple[float, float, float, float],
    page_number: int,
    item_number: int,
    item_bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    if sidecar.is_file():
        try:
            provenance = _FigureProvenance.model_validate_json(
                sidecar.read_text(encoding="utf-8"),
            )
        except (OSError, ValidationError):
            provenance = None
        if (
            provenance is not None
            and provenance.page_number == page_number
            and provenance.item_number == item_number
            and _contained(provenance.source_bbox, item_bbox)
        ):
            return provenance.source_bbox
    return image_bbox if _contained(image_bbox, item_bbox) else None


def single_layout_from_source(
    source_item: DetectedItem | None,
    artifacts: tuple[CropArtifact, ...],
    metadata: tuple[FigureAssetMetadata, ...],
) -> FigureLayout | None:
    if source_item is None or len(artifacts) != 1:
        return None
    bbox = resolve_source_bbox(
        artifacts[0].provenance_path, metadata[0].image_bbox,
        metadata[0].page_number, metadata[0].item_number, source_item.bbox,
    )
    source_pdf = metadata[0].source_pdf
    if bbox is None or not source_pdf.is_file():
        return None
    with fitz.open(source_pdf) as document:
        placement = classify_figure_placement(
            document[source_item.page_number - 1], source_item, (bbox,),
        )
    if placement is FigurePlacement.SIDE_BY_SIDE:
        return FigureLayout.ONE_SMALL
    if placement is FigurePlacement.BETWEEN_STEM_AND_ASK:
        return FigureLayout.ONE_LARGE
    return None


def _contained(
    bbox: tuple[float, float, float, float],
    item_bbox: tuple[float, float, float, float],
) -> bool:
    item = fitz.Rect(
        item_bbox[0] - 2.0, item_bbox[1] - 2.0,
        item_bbox[2] + 2.0, item_bbox[3] + 2.0,
    )
    candidate = fitz.Rect(bbox)
    return not candidate.is_empty and item.contains(candidate)


__all__ = ["resolve_source_bbox", "single_layout_from_source"]
