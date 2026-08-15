from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw

from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import DetectedItem, LayoutStyle


_REAL_SOURCE = Path("PDF/p1_2024_11.pdf")
_PANEL_TEXT = "그림 (가)와 (나)를 나타낸 것이다."


def _pixel_boundary(metadata: dict[str, Any], width: int) -> int:
    bbox = metadata["bbox"]
    panels = metadata["panel_bboxes"]
    assert isinstance(bbox, list) and isinstance(panels, list)
    left = panels[0]
    assert isinstance(left, list)
    return round((left[2] - bbox[0]) * width / (bbox[2] - bbox[0]))


def _cut_ink(image: Image.Image, boundary: int, bottom: int) -> int:
    pixels = image.load()
    return sum(pixels[x, y] < 235 for x in (boundary - 2, boundary - 1) for y in range(bottom))


def _blank_width(image: Image.Image, boundary: int, bottom: int) -> int:
    pixels = image.load()
    counts = [sum(pixels[x, y] < 235 for y in range(bottom)) for x in range(image.width)]
    start = boundary
    while start > 0 and counts[start - 1] == 0:
        start -= 1
    end = boundary
    while end < image.width and counts[end] == 0:
        end += 1
    return end - start


def test_real_item_7_caption_boundary_moves_off_right_panel_speed_label(tmp_path: Path) -> None:
    # Given: the caption-center midpoint crosses nine dark pixels of the right speed label.
    item = next(item for item in detect_items(_REAL_SOURCE).items if item.item_number == 7)
    with fitz.open(_REAL_SOURCE) as document:
        page = document[item.page_number - 1]
        raw = next(
            info for info in page.get_image_info(xrefs=True)
            if not (fitz.Rect(info["bbox"]) & fitz.Rect(item.bbox)).is_empty
        )
        with Image.open(BytesIO(document.extract_image(raw["xref"])["image"])) as opened:
            source = opened.convert("L")

    # When: the real figure is segmented and routed.
    draft = build_editable_draft(_REAL_SOURCE, item, tmp_path, layout_style=LayoutStyle.SUNEUNG)
    metadata = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))
    boundary = _pixel_boundary(metadata, source.width)
    caption_top = min(candidate["pixel_bbox"][1] for candidate in metadata["caption_candidates"])
    caption_centers = tuple(
        (candidate["bbox"][0] + candidate["bbox"][2]) / 2
        for candidate in metadata["caption_candidates"]
    )
    legacy_pdf_boundary = round(sum(caption_centers) / len(caption_centers), 3)
    legacy_pixel_boundary = round(
        (legacy_pdf_boundary - metadata["bbox"][0])
        * source.width / (metadata["bbox"][2] - metadata["bbox"][0])
    )

    # Then: caption evidence still proves two panels, but their boundary lies in a wide blank seam.
    assert _cut_ink(source, legacy_pixel_boundary, caption_top) == 9
    assert len(metadata["panel_bboxes"]) == 2
    assert _cut_ink(source, boundary, caption_top) == 0
    assert _blank_width(source, boundary, caption_top) >= 20


def test_caption_midpoint_is_refined_to_blank_seam_without_deciding_panel_count(tmp_path: Path) -> None:
    # Given: two authoritative caption groups whose midpoint bisects right-panel ink.
    image = Image.new("L", (400, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 160, 140), outline="black", width=2)
    draw.rectangle((205, 20, 380, 140), outline="black", width=2)
    draw.rectangle((195, 35, 204, 85), fill="black")
    for center in (80, 320):
        draw.rectangle((center - 25, 205, center - 18, 225), outline="black", width=2)
        draw.rectangle((center - 10, 205, center + 10, 225), outline="black", width=2)
        draw.rectangle((center + 18, 205, center + 25, 225), outline="black", width=2)
    stream = BytesIO()
    image.save(stream, format="PNG")
    source_pdf = tmp_path / "caption-midpoint-crosses-ink.pdf"
    image_bbox = (100.0, 80.0, 500.0, 320.0)
    source_bbox = (90.0, 70.0, 510.0, 330.0)
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_image(fitz.Rect(image_bbox), stream=stream.getvalue())
        document.save(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, _PANEL_TEXT)

    # When: the proven two-panel crop refines only its geometric seam.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    boundary = _pixel_boundary(metadata, image.width)
    caption_top = min(candidate["pixel_bbox"][1] for candidate in metadata["caption_candidates"])

    # Then: panel count still comes from the captions and the selected seam contains no ink.
    assert [candidate["text"] for candidate in metadata["caption_candidates"]] == ["(가)", "(나)"]
    assert len(metadata["panel_bboxes"]) == 2
    assert _cut_ink(image, boundary, caption_top) == 0
    assert 160 < boundary < 195
