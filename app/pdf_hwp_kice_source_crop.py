"""Conservative source-PDF prose observation for prepared figure crops."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

import fitz

from .pdf_hwp_pipeline_models import DetectedItem, FigureAsset
from .pdf_hwp_roundtrip_crop_audit import (
    CropSourceRequest,
    audit_crop_geometry,
    read_crop_geometry,
)


BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class SourceCropObservation:
    """Blocking prose rectangles, or why source geometry was unavailable."""

    contaminated_bboxes: tuple[BBox, ...]
    error: str | None = None


def has_drawings(page: fitz.Page, item: DetectedItem) -> bool:
    """Return whether vector geometry intersects a generated item rectangle."""
    return any(
        float(drawing["rect"].x1) >= item.bbox[0]
        and float(drawing["rect"].x0) <= item.bbox[2]
        and float(drawing["rect"].y1) >= item.bbox[1]
        and float(drawing["rect"].y0) <= item.bbox[3]
        for drawing in page.get_drawings()
    )


def observe_source_crop(asset: FigureAsset) -> SourceCropObservation:
    """Detect paragraph-like source text included in one prepared figure asset."""
    metadata = asset.metadata
    if not metadata.source_pdf.is_file():
        return SourceCropObservation(
            (), f"source PDF unavailable: {metadata.source_pdf}",
        )
    bboxes = (metadata.image_bbox,)
    payload: dict[str, object] = {}
    parent_name = re.sub(r"-figure-\d+\.png$", "-figure.json", asset.image_path.name)
    parent_sidecar = asset.image_path.with_name(parent_name)
    try:
        payload = json.loads(parent_sidecar.read_text(encoding="utf-8"))
        components = payload.get("component_bboxes")
        if isinstance(components, list) and components:
            bboxes = tuple(tuple(float(value) for value in bbox) for bbox in components)
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    if (
        payload.get("asset_mode") == "pdf_figure_mixed_region_crop_hd"
        and int(payload.get("image_count") or 0) == 0
        and bool(payload.get("protected_texts"))
        and not payload.get("excluded_body_spans")
        and payload.get("manual_review_required") is False
    ):
        # A proven vector-only material box intentionally owns its internal text.
        # Ordinary paragraph contamination has excluded_body_spans and must still block.
        return SourceCropObservation(())
    contaminated: list[BBox] = []
    try:
        with fitz.open(metadata.source_pdf) as document:
            page = document[metadata.page_number - 1]
            table_bboxes = tuple(
                fitz.Rect(table.bbox) for table in page.find_tables().tables
            )
        for bbox in bboxes:
            crop = fitz.Rect(bbox)
            if any(table.contains(crop) or crop.contains(table) for table in table_bboxes):
                continue
            geometry = read_crop_geometry(CropSourceRequest(
                source_pdf=metadata.source_pdf,
                page_number=metadata.page_number,
                item_number=metadata.item_number,
                item_bbox=metadata.image_bbox,
                crop_bbox=bbox,
                semantic_selection=True,
            ))
            contaminated.extend(audit_crop_geometry(geometry).contaminated_text_bboxes)
    except (fitz.FileDataError, FileNotFoundError, OSError, ValueError) as error:
        return SourceCropObservation((), str(error))
    return SourceCropObservation(tuple(contaminated))
