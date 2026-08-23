"""Geometry primitives shared by source and generated KICE figure checks."""
from __future__ import annotations

from enum import StrEnum
from io import BytesIO
from typing import Final, assert_never

import fitz
from PIL import Image

from .pdf_hwp_kice_structural_models import FigurePlacement
from .pdf_hwp_pipeline_models import DetectedItem, FigureArrangement, FigureAsset


BBox = tuple[float, float, float, float]
_GEOMETRY_TOLERANCE: Final = 1.0
_STEM_TOP_TOLERANCE: Final = 4.0
_SCALE_POINT_TOLERANCE: Final = 0.05


class _OrderAxis(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


def _contains(outer: BBox, inner: BBox) -> bool:
    return (inner[0] >= outer[0] - _GEOMETRY_TOLERANCE
            and inner[1] >= outer[1] - _GEOMETRY_TOLERANCE
            and inner[2] <= outer[2] + _GEOMETRY_TOLERANCE
            and inner[3] <= outer[3] + _GEOMETRY_TOLERANCE)


def _union(boxes: tuple[BBox, ...]) -> BBox:
    return (
        min(box[0] for box in boxes), min(box[1] for box in boxes),
        max(box[2] for box in boxes), max(box[3] for box in boxes),
    )


def _pre_ask_line_bboxes(
    page: fitz.Page,
    item: DetectedItem,
    ask: BBox,
) -> tuple[BBox, ...]:
    grouped: dict[tuple[int, int], list[BBox]] = {}
    for word in page.get_text("words", clip=fitz.Rect(item.bbox)):
        if not str(word[4]).strip() or float(word[1]) > ask[3] + _GEOMETRY_TOLERANCE:
            continue
        key = (int(word[5]), int(word[6]))
        grouped.setdefault(key, []).append(tuple(float(value) for value in word[:4]))
    return tuple(
        _union(tuple(words)) for words in grouped.values()
        if len(words) >= 2 or max(word[2] for word in words) - min(
            word[0] for word in words
        ) >= 36.0
    )


def _has_substantive_side_support(images: BBox, lines: tuple[BBox, ...]) -> bool:
    overlaps = tuple(
        min(images[3], line[3]) - max(images[1], line[1])
        for line in lines
        if max(line[0] - images[2], images[0] - line[2]) > _GEOMETRY_TOLERANCE
        and min(images[3], line[3]) - max(images[1], line[1]) > 0.0
    )
    return len(overlaps) >= 2 or sum(overlaps) >= 8.0


def classify_figure_placement(
    page: fitz.Page,
    item: DetectedItem,
    image_bboxes: tuple[BBox, ...],
) -> FigurePlacement | None:
    """Classify source or generated figures against the full question flow."""
    spans = tuple(
        (str(span[4]), tuple(float(value) for value in span[:4]))
        for span in page.get_text("words", clip=fitz.Rect(item.bbox))
        if str(span[4]).strip()
    )
    asks = tuple(box for text, box in spans if "?" in text)
    if not asks or not spans or not image_bboxes:
        return FigurePlacement.NONE if not image_bboxes else None
    ask = max(asks, key=lambda box: box[1])
    images = _union(image_bboxes)
    direct_overlap = min(images[3], ask[3]) - max(images[1], ask[1])
    direct_gutter = max(ask[0] - images[2], images[0] - ask[2])
    direct_side = (
        direct_overlap >= 8.0 and direct_gutter > _GEOMETRY_TOLERANCE
    )
    line_side = _has_substantive_side_support(
        images, _pre_ask_line_bboxes(page, item, ask),
    )
    if _contains(item.bbox, images) and (direct_side or line_side):
        return FigurePlacement.SIDE_BY_SIDE
    stem_top = min(spans, key=lambda value: (value[1][1], value[1][0]))[1][1]
    if all(bbox[1] >= ask[3] - _GEOMETRY_TOLERANCE for bbox in image_bboxes):
        return FigurePlacement.AFTER_ASK
    if all(bbox[3] <= ask[1] + _GEOMETRY_TOLERANCE for bbox in image_bboxes):
        return (FigurePlacement.BETWEEN_STEM_AND_ASK
                if all(bbox[1] >= stem_top - _STEM_TOP_TOLERANCE
                       for bbox in image_bboxes)
                else FigurePlacement.BEFORE_ASK)
    return FigurePlacement.BEFORE_ASK


def is_zero_information_raster(payload: bytes, has_mask: bool) -> bool:
    """Return true only for masked, effectively blank generated rasters."""
    if not has_mask:
        return False
    with Image.open(BytesIO(payload)) as opened:
        low, high = opened.convert("L").getextrema()
    return low >= 250 and high - low <= 2


def scale_is_readable(
    source_bbox: BBox,
    generated_bbox: BBox,
    minimum_scale: float,
) -> bool:
    """Apply a sub-pixel PDF point tolerance to the physical dimensions."""
    source_width = source_bbox[2] - source_bbox[0]
    source_height = source_bbox[3] - source_bbox[1]
    generated_width = generated_bbox[2] - generated_bbox[0]
    generated_height = generated_bbox[3] - generated_bbox[1]
    return (generated_width + _SCALE_POINT_TOLERANCE >= source_width * minimum_scale
            and generated_height + _SCALE_POINT_TOLERANCE
            >= source_height * minimum_scale)


def _source_axis(assets: tuple[FigureAsset, ...]) -> _OrderAxis:
    arrangement = assets[0].metadata.arrangement
    match arrangement:
        case FigureArrangement.HORIZONTAL:
            return _OrderAxis.HORIZONTAL
        case FigureArrangement.VERTICAL | FigureArrangement.GRID:
            return _OrderAxis.VERTICAL
        case FigureArrangement.COMPOSITE:
            centers = tuple((
                (asset.metadata.image_bbox[0] + asset.metadata.image_bbox[2]) / 2,
                (asset.metadata.image_bbox[1] + asset.metadata.image_bbox[3]) / 2,
            ) for asset in assets)
            horizontal_span = max(value[0] for value in centers) - min(
                value[0] for value in centers
            )
            vertical_span = max(value[1] for value in centers) - min(
                value[1] for value in centers
            )
            return (_OrderAxis.HORIZONTAL
                    if horizontal_span >= vertical_span else _OrderAxis.VERTICAL)
        case unreachable:
            assert_never(unreachable)


def geometric_order(
    image_bboxes: tuple[BBox, ...],
    assets: tuple[FigureAsset, ...],
) -> tuple[int, ...]:
    """Order panels on the typed source axis, ignoring orthogonal jitter."""
    axis = _source_axis(assets)
    match axis:
        case _OrderAxis.HORIZONTAL:
            coordinates = tuple((box[0], box[1]) for box in image_bboxes)
        case _OrderAxis.VERTICAL:
            coordinates = tuple((box[1], box[0]) for box in image_bboxes)
        case unreachable:
            assert_never(unreachable)
    return tuple(sorted(range(len(image_bboxes)), key=coordinates.__getitem__))


__all__ = [
    "classify_figure_placement", "geometric_order",
    "is_zero_information_raster", "scale_is_readable",
]
