"""Segment PDF figure objects without confusing nearby prose for diagram ink."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

import fitz
from PIL import Image, ImageDraw

from .pdf_hwp_figure_geometry import detect_arrangement, order_panel_bboxes
from .pdf_hwp_pipeline_models import FigureArrangement
from .pdf_hwp_raster_caption_segmentation import (
    caption_panel_bboxes,
    detect_raster_captions,
    expected_panel_labels,
    infer_three_panel_labels,
    panel_label_token,
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


def _group_image_panels(
    images: tuple[ImageObject, ...], expected_count: int,
) -> tuple[BBox, ...]:
    """Merge adjacent image objects into the semantic panel count."""
    boxes = tuple(image.bbox for image in images)
    if expected_count < 2 or len(boxes) <= expected_count:
        return boxes
    axis = classify_layout(boxes)
    ordered = _reading_order(boxes, axis)
    if axis not in {LayoutAxis.HORIZONTAL, LayoutAxis.VERTICAL}:
        return boxes
    gaps = tuple(
        right[0] - left[2] if axis is LayoutAxis.HORIZONTAL else right[1] - left[3]
        for left, right in zip(ordered, ordered[1:])
    )
    split_after = set(
        sorted(range(len(gaps)), key=lambda index: gaps[index], reverse=True)[: expected_count - 1]
    )
    groups: list[list[BBox]] = [[]]
    for index, box in enumerate(ordered):
        groups[-1].append(box)
        if index in split_after:
            groups.append([])
    return tuple(_union(tuple(group)) for group in groups if group)


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
    if image.xref <= 0:
        return _render_page(page, image.bbox)
    extracted = page.parent.extract_image(image.xref)
    with Image.open(BytesIO(extracted["image"])) as opened:
        return opened.convert("L")


def _png(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _pixel_box(pdf_box: BBox, image_size: tuple[int, int], image_bbox: BBox) -> PixelBBox:
    width, height = image_size
    x_scale = width / (image_bbox[2] - image_bbox[0])
    y_scale = height / (image_bbox[3] - image_bbox[1])
    return (
        round((pdf_box[0] - image_bbox[0]) * x_scale),
        round((pdf_box[1] - image_bbox[1]) * y_scale),
        round((pdf_box[2] - image_bbox[0]) * x_scale),
        round((pdf_box[3] - image_bbox[1]) * y_scale),
    )


def _order_panel_boxes(boxes: tuple[BBox, ...]) -> tuple[BBox, ...]:
    arrangement = detect_arrangement(boxes)
    if arrangement is FigureArrangement.HORIZONTAL:
        return _reading_order(boxes, LayoutAxis.HORIZONTAL)
    if arrangement is FigureArrangement.VERTICAL:
        return _reading_order(boxes, LayoutAxis.VERTICAL)
    ordered = order_panel_bboxes(boxes, arrangement)
    return ordered if ordered else _reading_order(boxes, classify_layout(boxes))


_PAREN_INNER_LABELS = {"가": "(가)", "나": "(나)", "다": "(다)"}


def _assembled_panel_labels(texts: tuple[TextRegion, ...]) -> tuple[TextRegion, ...]:
    """Join adjacent '(', '가|나|다', ')' spans that PDF dict extraction splits."""
    assembled: list[TextRegion] = []
    index = 0
    while index < len(texts):
        first = texts[index]
        inner = texts[index + 1] if index + 1 < len(texts) else None
        close = texts[index + 2] if index + 2 < len(texts) else None
        if (
            first.text == "("
            and inner is not None
            and close is not None
            and inner.text in _PAREN_INNER_LABELS
            and close.text.startswith(")")
            and abs(inner.bbox[1] - first.bbox[1]) <= 4
            and abs(close.bbox[1] - first.bbox[1]) <= 4
            and inner.bbox[0] >= first.bbox[2] - 2
            and close.bbox[0] >= inner.bbox[2] - 2
        ):
            assembled.append(TextRegion(
                _PAREN_INNER_LABELS[inner.text],
                (
                    first.bbox[0],
                    min(first.bbox[1], inner.bbox[1], close.bbox[1]),
                    close.bbox[2],
                    max(first.bbox[3], inner.bbox[3], close.bbox[3]),
                ),
            ))
            index += 3
            continue
        assembled.append(first)
        index += 1
    return tuple(assembled)


def _captions_under_panels(
    texts: tuple[TextRegion, ...],
    panels: tuple[BBox, ...],
    expected_labels: tuple[str, ...],
) -> tuple[CaptionRegion, ...]:
    if not expected_labels or len(expected_labels) != len(panels):
        return ()
    captions: list[CaptionRegion] = []
    used: set[int] = set()
    for panel, label in zip(panels, expected_labels, strict=True):
        below = (panel[3] - 2, panel[3] + 28)
        above = (panel[1] - 28, panel[1] + 2)
        matches = tuple(
            region for region in texts
            if id(region) not in used
            and panel_label_token(region.text, expected_labels) == label
            and _caption_in_panel_band(region.bbox, panel, below, above)
        )
        if not matches:
            return ()
        panel_center = (panel[0] + panel[2]) / 2
        chosen = min(
            matches,
            key=lambda region: (
                0 if _y_in_band(region.bbox, below) else 1,
                abs((region.bbox[0] + region.bbox[2]) / 2 - panel_center),
            ),
        )
        used.add(id(chosen))
        captions.append(CaptionRegion(
            label, chosen.bbox, (0, 0, 0, 0), "pdf_text", True, 1.0,
        ))
    return tuple(captions)


def _y_in_band(bbox: BBox, band: tuple[float, float]) -> bool:
    return band[0] < (bbox[1] + bbox[3]) / 2 <= band[1]


def _caption_in_panel_band(
    bbox: BBox,
    panel: BBox,
    below: tuple[float, float],
    above: tuple[float, float],
) -> bool:
    x_mid = (bbox[0] + bbox[2]) / 2
    y_mid = (bbox[1] + bbox[3]) / 2
    if panel[0] - 28 <= x_mid <= panel[2] + 8 and (
        _y_in_band(bbox, below) or _y_in_band(bbox, above)
    ):
        return True
    left = panel[0] - 28 <= x_mid < panel[0] + 2
    return left and panel[1] - 8 <= y_mid <= panel[3] + 8


def _raster_captions_for_panels(
    page: fitz.Page,
    panels: tuple[BBox, ...],
    expected_labels: tuple[str, ...],
) -> tuple[CaptionRegion, ...]:
    if len(panels) != len(expected_labels) or len(panels) < 2:
        return ()
    found: list[CaptionRegion] = []
    for panel, label in zip(panels, expected_labels, strict=True):
        rendered = _render_page(page, panel)
        detected, unsafe = detect_raster_captions(rendered, panel, (label,))
        if unsafe or len(detected) != 1 or not detected[0].excluded:
            return ()
        caption = detected[0]
        found.append(CaptionRegion(
            label, caption.bbox, caption.pixel_bbox, "raster_geometry", True, caption.confidence,
        ))
    return tuple(found)


def _exclude_raster_captions(
    rendered: Image.Image,
    image_bbox: BBox,
    captions: tuple[CaptionRegion, ...],
) -> Image.Image:
    raster = tuple(
        caption for caption in captions
        if caption.detection_source == "raster_geometry" and caption.excluded
    )
    if not raster:
        return rendered
    draw = ImageDraw.Draw(rendered)
    for caption in raster:
        draw.rectangle(_pixel_box(caption.bbox, rendered.size, image_bbox), fill="white")
    return rendered


def _trim_panels_to_captions(
    panels: tuple[BBox, ...], captions: tuple[CaptionRegion, ...],
) -> tuple[BBox, ...]:
    if len(panels) != len(captions):
        return panels
    trimmed = []
    for panel, caption in zip(panels, captions, strict=True):
        cap = caption.bbox
        if panel[1] < cap[1] < panel[3]:
            trimmed.append((panel[0], panel[1], panel[2], cap[1]))
        elif panel[1] < cap[3] < panel[3]:
            trimmed.append((panel[0], cap[3], panel[2], panel[3]))
        else:
            trimmed.append(panel)
    valid = tuple(box for box in trimmed if box[2] > box[0] and box[3] > box[1])
    return valid if len(valid) == len(panels) else panels


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
    image_bbox = _union(tuple(image.bbox for image in images)) if images else _bbox(source)
    text_clip = fitz.Rect(source)
    text_clip.y1 = max(float(text_clip.y1), image_bbox[3] + 22)
    text_clip.x0 = min(float(text_clip.x0), image_bbox[0] - 24)
    texts = _page_text(page, _bbox(text_clip))
    selected = fitz.Rect(image_bbox)
    drawings = tuple(
        drawing for drawing in page.get_drawings()
        if not (drawing["rect"] & selected).is_empty
    )
    excluded = tuple(region for region in texts if (fitz.Rect(region.bbox) & fitz.Rect(image_bbox)).is_empty)
    protected = tuple(region.text for region in texts if region not in excluded)
    if len(images) == 1:
        vector = select_vector_figure(page, _bbox(source))
        vector_box = fitz.Rect(vector.selected_bbox)
        image_box = fitz.Rect(image_bbox)
        mixed_component = (
            vector.drawing_count > 0
            and not (vector_box & image_box).is_empty
            and (
                vector_box.width > image_box.width * 2
                or vector_box.height > image_box.height * 2
            )
        )
        if mixed_component:
            mixed_box = vector_box | image_box
            rendered = _render_page(page, _bbox(mixed_box))
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
                _bbox(mixed_box),
                LayoutAxis.SINGLE,
                (_bbox(mixed_box),),
                captions,
                tuple(TextRegion(region.text, region.bbox) for region in vector.excluded_body),
                tuple(region.text for region in vector.protected),
                vector.drawing_count,
                1,
                vector.text_span_count,
                False,
                (),
                "pdf_text" if captions else "none",
                "pdf_text" if captions else "none",
            )
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
        unsafe = False
        panels = (
            (image_bbox,)
            if _is_tiled_surface(images)
            else _group_image_panels(images, len(expected_labels))
        )
        panels = _order_panel_boxes(panels)
        caption_texts = _assembled_panel_labels(texts)
        labels = expected_labels or infer_three_panel_labels(
            tuple((region.text, region.bbox) for region in caption_texts), panels,
        )
        captions = _captions_under_panels(caption_texts, panels, labels)
        if not captions and labels:
            captions = _raster_captions_for_panels(page, panels, labels)
        if captions:
            rendered = _exclude_raster_captions(rendered, image_bbox, captions)
            panels = _trim_panels_to_captions(panels, captions)
        axis = classify_layout(panels)
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
        captions[0].detection_source if captions else "none",
        "pdf_text" if captions and captions[0].detection_source == "pdf_text" else
        "item_source_text" if any(caption.text for caption in captions) else "none",
    )
