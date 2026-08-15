"""Segment PDF figure objects without confusing nearby prose for diagram ink."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

import fitz
from PIL import Image, ImageDraw

from .pdf_hwp_raster_caption_segmentation import (
    caption_panel_bboxes,
    detect_raster_captions,
    expected_panel_labels,
)
from .pdf_hwp_vector_segmentation import select_vector_figure


BBox = tuple[float, float, float, float]
PixelBBox = tuple[int, int, int, int]


class LayoutAxis(StrEnum):
    SINGLE = "single"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class EmptyFigureSelectionError(RuntimeError):
    source_bbox: BBox
    selected_bbox: BBox

    def __str__(self) -> str:
        return f"figure selection has no area: {self.selected_bbox} within {self.source_bbox}"


@dataclass(frozen=True, slots=True)
class TextRegion:
    text: str
    bbox: BBox


@dataclass(frozen=True, slots=True)
class CaptionRegion:
    text: str
    bbox: BBox
    pixel_bbox: PixelBBox
    detection_source: str
    excluded: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class ImageObject:
    xref: int
    bbox: BBox
    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class FigureSegmentation:
    image_png: bytes
    source_bbox: BBox
    image_bbox: BBox
    layout_axis: LayoutAxis
    panel_bboxes: tuple[BBox, ...]
    captions: tuple[CaptionRegion, ...]
    excluded_body_spans: tuple[TextRegion, ...]
    protected_texts: tuple[str, ...]
    drawing_count: int
    image_count: int
    text_span_count: int
    manual_review_required: bool
    review_reasons: tuple[str, ...]
    caption_detection_source: str
    caption_text_source: str

    @property
    def component_count(self) -> int:
        return self.drawing_count + self.image_count + self.text_span_count


def _bbox(rect: fitz.Rect) -> BBox:
    return tuple(round(float(value), 3) for value in rect)


def _union(boxes: tuple[BBox, ...]) -> BBox:
    merged = fitz.Rect(boxes[0])
    for box in boxes[1:]:
        merged |= fitz.Rect(box)
    return _bbox(merged)


def classify_layout(boxes: tuple[BBox, ...]) -> LayoutAxis:
    """Classify panel arrangement from source-coordinate object centers."""
    if len(boxes) <= 1:
        return LayoutAxis.SINGLE
    centers_x = tuple((box[0] + box[2]) / 2 for box in boxes)
    centers_y = tuple((box[1] + box[3]) / 2 for box in boxes)
    x_spread = max(centers_x) - min(centers_x)
    y_spread = max(centers_y) - min(centers_y)
    return LayoutAxis.HORIZONTAL if x_spread >= y_spread else LayoutAxis.VERTICAL


def _reading_order(boxes: tuple[BBox, ...], axis: LayoutAxis) -> tuple[BBox, ...]:
    if axis is LayoutAxis.HORIZONTAL:
        return tuple(sorted(boxes, key=lambda box: (box[0], box[1])))
    if axis is LayoutAxis.VERTICAL:
        return tuple(sorted(boxes, key=lambda box: (box[1], box[0])))
    return boxes


def _page_text(page: fitz.Page, source: fitz.Rect) -> tuple[TextRegion, ...]:
    regions: list[TextRegion] = []
    for block in page.get_text("dict", clip=source)["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                text = str(span.get("text", "")).strip()
                if text:
                    regions.append(TextRegion(text, _bbox(fitz.Rect(span["bbox"]))))
    return tuple(regions)


def _image_objects(page: fitz.Page, source: fitz.Rect) -> tuple[ImageObject, ...]:
    objects: list[ImageObject] = []
    for raw in page.get_image_info(xrefs=True):
        box = fitz.Rect(raw["bbox"])
        overlap = box & source
        if overlap.is_empty or overlap.get_area() < box.get_area() * 0.5:
            continue
        objects.append(ImageObject(
            int(raw["xref"]),
            _bbox(box),
            int(raw["width"]),
            int(raw["height"]),
        ))
    return tuple(sorted(objects, key=lambda value: (value.bbox[1], value.bbox[0])))


def _is_tiled_surface(images: tuple[ImageObject, ...]) -> bool:
    tolerance = 0.5
    vertical = (
        len({image.width_px for image in images}) == 1
        and all(
            abs(left.bbox[0] - right.bbox[0]) <= tolerance
            and abs(left.bbox[2] - right.bbox[2]) <= tolerance
            and abs(left.bbox[3] - right.bbox[1]) <= tolerance
            for left, right in zip(images, images[1:])
        )
    )
    horizontal = (
        len({image.height_px for image in images}) == 1
        and all(
            abs(left.bbox[1] - right.bbox[1]) <= tolerance
            and abs(left.bbox[3] - right.bbox[3]) <= tolerance
            and abs(left.bbox[2] - right.bbox[0]) <= tolerance
            for left, right in zip(images, images[1:])
        )
    )
    return vertical or horizontal


def _render_page(page: fitz.Page, bbox: BBox) -> Image.Image:
    pixmap = page.get_pixmap(dpi=300, clip=fitz.Rect(bbox), alpha=False, colorspace=fitz.csGRAY)
    return Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)


def _extract_image(page: fitz.Page, image: ImageObject) -> Image.Image:
    extracted = page.parent.extract_image(image.xref)
    with Image.open(BytesIO(extracted["image"])) as opened:
        return opened.convert("L")


def _png(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def segment_figure(
    page: fitz.Page,
    source_bbox: BBox,
    expected_labels: tuple[str, ...],
) -> FigureSegmentation:
    """Return a lossless object crop plus explicit caption/body decisions."""
    source = fitz.Rect(source_bbox)
    images = _image_objects(page, source)
    if not images:
        vector = select_vector_figure(page, _bbox(source))
        selected = fitz.Rect(vector.selected_bbox)
        if selected.is_empty:
            raise EmptyFigureSelectionError(_bbox(source), vector.selected_bbox)
        rendered = _render_page(page, vector.selected_bbox)
        captions = tuple(CaptionRegion(
            region.text,
            region.bbox,
            (0, 0, 0, 0),
            "pdf_text",
            True,
            1.0,
        ) for region in vector.captions)
        return FigureSegmentation(
            _png(rendered),
            _bbox(source),
            vector.selected_bbox,
            LayoutAxis.SINGLE,
            (vector.selected_bbox,),
            captions,
            tuple(TextRegion(region.text, region.bbox) for region in vector.excluded_body),
            tuple(region.text for region in vector.protected),
            vector.drawing_count,
            0,
            vector.text_span_count,
            False,
            (),
            "pdf_text" if captions else "none",
            "pdf_text" if captions else "none",
        )
    texts = _page_text(page, source)
    image_bbox = _union(tuple(image.bbox for image in images)) if images else _bbox(source)
    selected = fitz.Rect(image_bbox)
    drawings = tuple(
        drawing for drawing in page.get_drawings()
        if not (drawing["rect"] & selected).is_empty
    )
    excluded = tuple(region for region in texts if (fitz.Rect(region.bbox) & fitz.Rect(image_bbox)).is_empty)
    protected = tuple(region.text for region in texts if region not in excluded)
    if len(images) == 1:
        rendered = _extract_image(page, images[0])
        detected, unsafe = detect_raster_captions(rendered, image_bbox, expected_labels)
        captions = tuple(CaptionRegion(
            caption.text,
            caption.bbox,
            caption.pixel_bbox,
            "raster_geometry",
            caption.excluded,
            caption.confidence,
        ) for caption in detected)
        if captions and not unsafe:
            draw = ImageDraw.Draw(rendered)
            for caption in captions:
                draw.rectangle(caption.pixel_bbox, fill="white")
        panels = caption_panel_bboxes(rendered, detected, image_bbox) if detected and not unsafe else (image_bbox,)
        axis = LayoutAxis.HORIZONTAL if len(panels) > 1 else LayoutAxis.UNSAFE if unsafe else LayoutAxis.SINGLE
    else:
        rendered = _render_page(page, image_bbox)
        captions = ()
        unsafe = False
        panels = (image_bbox,) if _is_tiled_surface(images) else tuple(image.bbox for image in images)
        axis = classify_layout(panels)
        panels = _reading_order(panels, axis)
    return FigureSegmentation(
        _png(rendered),
        _bbox(source),
        image_bbox,
        axis,
        panels,
        captions,
        excluded,
        protected,
        len(drawings),
        len(images),
        len(texts),
        unsafe,
        ("ambiguous_raster_caption",) if unsafe else (),
        "raster_geometry" if captions else "none",
        "item_source_text" if any(caption.text for caption in captions) else "none",
    )
