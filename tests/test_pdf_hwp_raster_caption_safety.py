from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

import fitz
from PIL import Image, ImageChops, ImageDraw

from app.pdf_hwp_crop_assets import crop_region
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import DetectedItem, LayoutStyle


_REAL_SOURCE = Path("PDF/p1_2024_11.pdf")


def test_semantic_ink_below_caption_candidates_forces_lossless_manual_review(tmp_path: Path) -> None:
    # Given: equal caption-like groups are followed by required semantic ink.
    image = Image.new("L", (400, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 25, 365, 130), outline="black", width=2)
    for center in (105, 295):
        draw.rectangle((center - 25, 175, center - 18, 192), outline="black", width=2)
        draw.rectangle((center - 10, 175, center + 10, 192), outline="black", width=2)
        draw.rectangle((center + 18, 175, center + 25, 192), outline="black", width=2)
    draw.line((120, 224, 280, 224), fill="black", width=3)
    stream = BytesIO()
    image.save(stream, format="PNG")
    source_png = stream.getvalue()
    source_bbox = (80.0, 60.0, 520.0, 340.0)
    image_bbox = (100.0, 80.0, 500.0, 320.0)
    source_pdf = tmp_path / "caption-before-semantic.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_image(fitz.Rect(image_bbox), stream=source_png)
        document.save(source_pdf)
    item = DetectedItem(1, 7, 0, source_bbox, "그림 (가)와 (나)를 나타낸 것이다.")

    # When: raster segmentation cannot prove that the candidate row is terminal.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: every source pixel survives and the uncertainty is routed to review.
    assert metadata["manual_review_required"] is True
    assert metadata["review_reasons"] == ["ambiguous_raster_caption"]
    assert [caption["excluded"] for caption in metadata["caption_candidates"]] == [False, False]
    assert ImageChops.difference(
        Image.open(BytesIO(source_png)).convert("L"),
        Image.open(artifact.image_path).convert("L"),
    ).getbbox() is None


def test_real_item_1_tiled_xrefs_remain_one_semantic_composite(tmp_path: Path) -> None:
    # Given: item 1 is stored as two equal-width raster tiles sharing an exact horizontal seam.
    item = next(item for item in detect_items(_REAL_SOURCE).items if item.item_number == 1)

    # When: the source objects are segmented and routed.
    draft = build_editable_draft(_REAL_SOURCE, item, tmp_path, layout_style=LayoutStyle.SUNEUNG)
    source = json.loads(draft.figure_asset.provenance_path.read_text(encoding="utf-8"))

    # Then: storage tiling cannot invent two semantic panels.
    assert source["image_count"] == 2
    assert source["panel_bboxes"] == [source["bbox"]]
    assert source["layout_axis"] == "single"
    assert len(draft.figure_assets) == 1


def test_near_abutting_aligned_raster_tiles_merge_without_caption_evidence(tmp_path: Path) -> None:
    # Given: equal-width source bands reconstruct one continuous raster across a sub-point seam.
    top = Image.new("L", (200, 100), "white")
    bottom = Image.new("L", (200, 100), "white")
    ImageDraw.Draw(top).line((100, 10, 100, 99), fill="black", width=3)
    ImageDraw.Draw(bottom).line((100, 0, 100, 90), fill="black", width=3)
    streams: list[bytes] = []
    for tile in (top, bottom):
        stream = BytesIO()
        tile.save(stream, format="PNG")
        streams.append(stream.getvalue())
    source_pdf = tmp_path / "near-abutting-tiles.pdf"
    source_bbox = (90.0, 70.0, 310.0, 220.0)
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        page.insert_image(fitz.Rect(100, 80, 300, 140), stream=streams[0])
        page.insert_image(fitz.Rect(100, 140.25, 300, 200.25), stream=streams[1])
        document.save(source_pdf)
    item = DetectedItem(1, 1, 0, source_bbox, "하나의 연속 그림이다.")

    # When: storage tiles are segmented with no caption or semantic-panel evidence.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: the near seam is one panel; separated H/V layouts are covered by the layout test.
    assert metadata["image_count"] == 2
    assert metadata["panel_bboxes"] == [metadata["bbox"]]
    assert metadata["layout_axis"] == "single"


def test_staggered_horizontal_raster_objects_are_ordered_left_to_right(tmp_path: Path) -> None:
    # Given: the right panel is slightly higher, so generic (y, x) sorting is wrong.
    panel = Image.new("L", (80, 60), "white")
    ImageDraw.Draw(panel).rectangle((5, 5, 74, 54), outline="black", width=3)
    stream = BytesIO()
    panel.save(stream, format="PNG")
    left = (80.0, 100.0, 160.0, 160.0)
    right = (240.0, 92.5, 320.0, 152.5)
    source_pdf = tmp_path / "staggered-horizontal.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=260)
        page.insert_image(fitz.Rect(right), stream=stream.getvalue())
        page.insert_image(fitz.Rect(left), stream=stream.getvalue())
        document.save(source_pdf)
    source_bbox = tuple(fitz.Rect(left) | fitz.Rect(right))
    item = DetectedItem(1, 14, 0, source_bbox, "왼쪽과 오른쪽 그림이다.")

    # When: panel order is derived after arrangement classification.
    artifact = crop_region(source_pdf, item, source_bbox, tmp_path / "output", "figure")
    metadata = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))

    # Then: horizontal reading order is authoritative regardless of vertical stagger.
    assert metadata["layout_axis"] == "horizontal"
    assert metadata["panel_bboxes"] == [list(left), list(right)]
