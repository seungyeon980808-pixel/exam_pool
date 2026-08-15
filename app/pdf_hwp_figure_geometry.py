"""Pure geometry classifiers for PDF figure panel routing."""
from __future__ import annotations

from .pdf_hwp_pipeline_models import BoundingBox, FigureArrangement


def _separated(boxes: tuple[BoundingBox, ...], axis: int) -> bool:
    ordered = sorted(boxes, key=lambda box: box[axis])
    far_edge = axis + 2
    return all(left[far_edge] <= right[axis] for left, right in zip(ordered, ordered[1:]))


def _overlaps(boxes: tuple[BoundingBox, ...]) -> bool:
    return any(
        left[0] < right[2] and right[0] < left[2]
        and left[1] < right[3] and right[1] < left[3]
        for index, left in enumerate(boxes)
        for right in boxes[index + 1:]
    )


def detect_arrangement(boxes: tuple[BoundingBox, ...]) -> FigureArrangement:
    """Classify ordered panel boxes independently of raster/vector source mode."""
    if len(boxes) == 1:
        return FigureArrangement.COMPOSITE
    if _separated(boxes, 0):
        return FigureArrangement.HORIZONTAL
    if _separated(boxes, 1):
        return FigureArrangement.VERTICAL
    if len(boxes) == 3 and not _overlaps(boxes):
        return FigureArrangement.GRID
    return FigureArrangement.COMPOSITE


def order_panel_bboxes(
    boxes: tuple[BoundingBox, ...], arrangement: FigureArrangement,
) -> tuple[BoundingBox, ...]:
    """Return stable spatial reading order independent of PDF object enumeration."""
    axis_order = (0, 1) if arrangement is FigureArrangement.HORIZONTAL else (1, 0)
    return tuple(sorted(boxes, key=lambda box: (box[axis_order[0]], box[axis_order[1]])))


def expected_panel_count(passage: str) -> int:
    if all(label in passage for label in ("(가)", "(나)", "(다)")):
        return 3
    if "(가)" in passage and "(나)" in passage:
        return 2
    return 1
