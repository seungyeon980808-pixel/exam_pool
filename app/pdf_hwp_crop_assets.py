"""Render source-PDF regions into provenance-complete PNG assets."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path

import fitz
from PIL import Image

from .pdf_hwp_object_segmentation import segment_figure
from .pdf_hwp_pipeline_models import CropArtifact, DetectedItem
from .pdf_hwp_raster_caption_segmentation import expected_panel_labels


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crop_region(
    source_pdf: Path,
    item: DetectedItem,
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    label: str,
) -> CropArtifact:
    """Render one object-segmented PNG crop with source coordinates and hashes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"page-{item.page_number}-item-{item.item_number}-{label}.png"
    sidecar = image_path.with_suffix(".json")
    with fitz.open(source_pdf) as document:
        page = document[item.page_number - 1]
        segmented = segment_figure(page, bbox, expected_panel_labels(item.source_text))
    image_path.write_bytes(segmented.image_png)
    with Image.open(BytesIO(segmented.image_png)) as image:
        width_px, height_px = image.size
    payload = {
        "asset_mode": f"pdf_{label}_object_crop_hd",
        "source_pdf": str(source_pdf.resolve()),
        "source_hash": _sha256(source_pdf),
        "page_number": item.page_number,
        "item_number": item.item_number,
        "source_bbox": list(segmented.source_bbox),
        "bbox": list(segmented.image_bbox),
        "dpi": 300,
        "width_px": width_px,
        "height_px": height_px,
        "asset_hash": _sha256(image_path),
        "segmentation_method": "pdf_objects_v1",
        "layout_axis": segmented.layout_axis.value,
        "panel_bboxes": [list(box) for box in segmented.panel_bboxes],
        "component_count": segmented.component_count,
        "drawing_count": segmented.drawing_count,
        "image_count": segmented.image_count,
        "text_span_count": segmented.text_span_count,
        "protected_texts": list(segmented.protected_texts),
        "excluded_texts": [region.text for region in segmented.excluded_body_spans],
        "excluded_body_spans": [
            {"text": region.text, "bbox": list(region.bbox)}
            for region in segmented.excluded_body_spans
        ],
        "caption_candidates": [
            {
                "text": caption.text,
                "bbox": list(caption.bbox),
                "pixel_bbox": list(caption.pixel_bbox),
                "detection_source": caption.detection_source,
                "excluded": caption.excluded,
                "confidence": caption.confidence,
            }
            for caption in segmented.captions
        ],
        "caption_detection_source": segmented.caption_detection_source,
        "caption_text_source": segmented.caption_text_source,
        "manual_review_required": segmented.manual_review_required,
        "review_reasons": list(segmented.review_reasons),
        "debug_overlay_legend": {
            "selected_object": "green",
            "panel": "blue",
            "caption": "orange",
            "excluded_body": "red",
        },
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CropArtifact(image_path, sidecar, width_px, height_px)
