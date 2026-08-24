from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_object_segmentation import expected_panel_labels, segment_figure
from app.pdf_hwp_pipeline_models import DetectedItem
from app.pdf_hwp_segmentation_debug import write_debug_overlay


def test_debug_overlay_is_explicit_and_kept_out_of_production_sidecar(tmp_path: Path) -> None:
    # Given: one extraction contains selected, panel, caption, and excluded-body evidence.
    image = Image.new("L", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 380, 145), outline="black", width=2)
    for center in (105, 295):
        draw.rectangle((center - 25, 175, center - 18, 192), outline="black", width=2)
        draw.rectangle((center - 10, 175, center + 10, 192), outline="black", width=2)
        draw.rectangle((center + 18, 175, center + 25, 192), outline="black", width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    source_bbox = (90.0, 70.0, 510.0, 310.0)
    image_bbox = (100.0, 80.0, 500.0, 280.0)
    source_pdf = tmp_path / "overlay-source.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_image(fitz.Rect(image_bbox), stream=stream.getvalue())
        page.insert_text((110, 300), "BODY OUTSIDE IMAGE", fontsize=12)
        document.save(source_pdf)
    source_text = "그림 (가)와 (나)를 나타낸 것이다."
    item = DetectedItem(1, 7, 0, source_bbox, source_text)

    # When: production extraction and a separate explicit debug operation run.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    overlay_path = tmp_path / "evidence" / "segmentation-overlay.png"
    overlay_path.parent.mkdir()
    with fitz.open(source_pdf) as document:
        result = segment_figure(document[0], source_bbox, expected_panel_labels(source_text))
        write_debug_overlay(document[0], result, overlay_path)

    # Then: only the evidence directory contains an overlay; production records a stable legend.
    assert "debug_overlay_path" not in metadata
    assert overlay_path.is_file()
    assert metadata["debug_overlay_legend"] == {
        "selected_object": "green", "panel": "blue", "caption": "orange", "excluded_body": "red",
    }
    with Image.open(overlay_path) as opened:
        colors = set(opened.convert("RGB").get_flattened_data())
    assert {(0, 160, 0), (0, 90, 220), (255, 140, 0), (220, 0, 0)} <= colors
