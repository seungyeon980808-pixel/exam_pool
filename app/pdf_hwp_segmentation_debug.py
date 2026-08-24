"""Render source-coordinate evidence overlays for PDF figure segmentation."""
from __future__ import annotations

from pathlib import Path
from typing import Final

import fitz
from PIL import Image, ImageDraw

from .pdf_hwp_object_segmentation import BBox, FigureSegmentation, PixelBBox


_SELECTED: Final = (0, 160, 0)
_PANEL: Final = (0, 90, 220)
_CAPTION: Final = (255, 140, 0)
_EXCLUDED: Final = (220, 0, 0)


def _pixel_box(box: BBox, source: BBox, image_size: tuple[int, int]) -> PixelBBox:
    width, height = image_size
    x_scale = width / (source[2] - source[0])
    y_scale = height / (source[3] - source[1])
    return tuple(round(value) for value in (
        (box[0] - source[0]) * x_scale,
        (box[1] - source[1]) * y_scale,
        (box[2] - source[0]) * x_scale,
        (box[3] - source[1]) * y_scale,
    ))


def write_debug_overlay(page: fitz.Page, result: FigureSegmentation, output_path: Path) -> None:
    """Draw each segmentation decision over the originally requested page crop."""
    pixmap = page.get_pixmap(dpi=144, clip=fitz.Rect(result.source_bbox), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    draw = ImageDraw.Draw(image)
    selected = _pixel_box(result.image_bbox, result.source_bbox, image.size)
    draw.rectangle(selected, outline=_SELECTED, width=5)
    for panel in result.panel_bboxes:
        draw.rectangle(_pixel_box(panel, result.source_bbox, image.size), outline=_PANEL, width=2)
    for caption in result.captions:
        draw.rectangle(_pixel_box(caption.bbox, result.source_bbox, image.size), outline=_CAPTION, width=3)
    for region in result.excluded_body_spans:
        draw.rectangle(_pixel_box(region.bbox, result.source_bbox, image.size), outline=_EXCLUDED, width=3)
    image.save(output_path, format="PNG")
