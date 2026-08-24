from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TypedDict

from PIL import Image, ImageDraw

from app import pdf_hwp_pipeline_models as models


class CaptionPayload(TypedDict):
    text: str
    bbox: models.BoundingBox


@dataclass(frozen=True, slots=True)
class SourceArtifactSpec:
    name: str
    panel_bboxes: tuple[models.BoundingBox, ...]
    captions: tuple[CaptionPayload, ...]
    drawing_count: int
    image_count: int
    manual_review_required: bool = False
    report_panel_bboxes: bool = True
    component_count: int = 0


def source_artifact(root: Path, spec: SourceArtifactSpec) -> models.CropArtifact:
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / f"{spec.name}.png"
    image = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(image)
    for box in spec.panel_bboxes:
        draw.rectangle((box[0] + 10, box[1] + 10, box[2] - 10, box[3] - 30), fill="black")
    for caption in spec.captions:
        box = caption["bbox"]
        draw.rectangle(box, fill="black")
    image.save(image_path)
    source_pdf = root / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.7\n")
    provenance_path = image_path.with_suffix(".json")
    provenance_path.write_text(
        json.dumps(
            {
                "asset_mode": "pdf_figure_crop_hd",
                "source_pdf": str(source_pdf),
                "page_number": 2,
                "item_number": 7,
                "bbox": [0.0, 0.0, 300.0, 300.0],
                "source_bbox": [0.0, 0.0, 300.0, 300.0],
                "dpi": 300,
                "width_px": 300,
                "height_px": 300,
                "asset_hash": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                "layout_axis": "unsafe",
                "drawing_count": spec.drawing_count,
                "image_count": spec.image_count,
                "component_count": spec.component_count,
                "panel_bboxes": spec.panel_bboxes if spec.report_panel_bboxes else (),
                "caption_candidates": spec.captions,
                "manual_review_required": spec.manual_review_required,
                "review_reasons": ["unsafe segmentation"] if spec.manual_review_required else [],
            }
        ),
        encoding="utf-8",
    )
    return models.CropArtifact(image_path, provenance_path, image.width, image.height)
