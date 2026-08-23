"""Pure geometry classifiers for PDF figure panel routing."""
from __future__ import annotations

from .pdf_hwp_pipeline_models import BoundingBox, FigureArrangement
from .pdf_hwp_raster_caption_segmentation import expected_panel_labels


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


def _range_overlap(left: BoundingBox, right: BoundingBox, axis: int) -> float:
    far = axis + 2
    return min(left[far], right[far]) - max(left[axis], right[axis])


def _aligned_pair_and_third(
    boxes: tuple[BoundingBox, ...], axis: int,
) -> tuple[int, int, int] | None:
    """Two boxes aligned on `axis` with the third separated from both."""
    if len(boxes) != 3:
        return None
    found: tuple[int, int, int] | None = None
    for first in range(3):
        for second in range(first + 1, 3):
            third = 3 - first - second
            if _range_overlap(boxes[first], boxes[second], axis) <= 0:
                continue
            if (
                _range_overlap(boxes[first], boxes[third], axis) <= 0
                and _range_overlap(boxes[second], boxes[third], axis) <= 0
            ):
                if found is not None:
                    return None
                found = (first, second, third)
    return found


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
    if arrangement is FigureArrangement.GRID:
        ordered = _order_grid_bboxes(boxes)
        if ordered is not None:
            return ordered
    axis_order = (0, 1) if arrangement is FigureArrangement.HORIZONTAL else (1, 0)
    return tuple(sorted(boxes, key=lambda box: (box[axis_order[0]], box[axis_order[1]])))


def _order_grid_bboxes(boxes: tuple[BoundingBox, ...]) -> tuple[BoundingBox, ...] | None:
    """Read L-shaped three-panel grids as (가)(나)(다), not raw (y, x)."""
    column = _aligned_pair_and_third(boxes, 0)
    row = _aligned_pair_and_third(boxes, 1)
    if column is not None and row is None:
        first, second, third = column
        pair = tuple(sorted((boxes[first], boxes[second]), key=lambda box: box[1]))
        lone = boxes[third]
        pair_center = (pair[0][0] + pair[0][2] + pair[1][0] + pair[1][2]) / 4
        if (lone[0] + lone[2]) / 2 < pair_center:
            return (lone, *pair)
        return (*pair, lone)
    if row is not None and column is None:
        first, second, third = row
        pair = tuple(sorted((boxes[first], boxes[second]), key=lambda box: box[0]))
        lone = boxes[third]
        pair_center = (pair[0][1] + pair[0][3] + pair[1][1] + pair[1][3]) / 4
        if (lone[1] + lone[3]) / 2 < pair_center:
            return (lone, *pair)
        return (*pair, lone)
    return None


def expected_panel_count(passage: str) -> int:
    labels = expected_panel_labels(passage)
    return len(labels) if labels else 1
