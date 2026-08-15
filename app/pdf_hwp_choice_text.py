"""Read five KICE text choices independently of their row/column layout."""
from __future__ import annotations

from statistics import median
from typing import Protocol


CIRCLED = "①②③④⑤"


class ChoiceWord(Protocol):
    bbox: tuple[float, float, float, float]
    text: str
    suppressed: bool


def _clusters(values: tuple[float, ...], tolerance: float) -> tuple[float, ...]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return tuple(sum(group) / len(group) for group in groups)


def _nearest(value: float, centers: tuple[float, ...]) -> int:
    return min(range(len(centers)), key=lambda index: abs(value - centers[index]))


def _within_outer_row(value: float, centers: tuple[float, ...]) -> bool:
    if len(centers) == 1:
        return centers[0] - 8 <= value <= centers[0] + 24
    gap = median(right - left for left, right in zip(centers, centers[1:]))
    return centers[0] - gap / 2 <= value <= centers[-1] + gap / 2


def choice_texts(words: list[ChoiceWord], start_y: float) -> tuple[str, ...]:
    """Return choices in marker order for horizontal, vertical, or grid layouts."""
    visible = tuple(word for word in words if not word.suppressed)
    markers: list[ChoiceWord] = []
    for marker in CIRCLED:
        matches = tuple(word for word in visible if marker in word.text)
        if len(matches) != 1:
            return ()
        markers.append(matches[0])
    if abs(markers[0].bbox[1] - start_y) > 8:
        return ()

    row_centers = _clusters(tuple(word.bbox[1] for word in markers), 12.0)
    column_centers = _clusters(tuple(word.bbox[0] for word in markers), 20.0)
    marker_cells = {
        (_nearest(word.bbox[1], row_centers), _nearest(word.bbox[0], column_centers)): index
        for index, word in enumerate(markers)
    }
    marker_suffixes = tuple(word.text.split(marker, 1)[1] for marker, word in zip(CIRCLED, markers))
    parts: list[list[ChoiceWord]] = [[] for _ in CIRCLED]
    for word in visible:
        if word in markers or not _within_outer_row(word.bbox[1], row_centers):
            continue
        cell = (
            _nearest(word.bbox[1], row_centers),
            _nearest(word.bbox[0], column_centers),
        )
        marker_index = marker_cells.get(cell)
        if marker_index is None or word.bbox[0] < markers[marker_index].bbox[2] - 1:
            continue
        parts[marker_index].append(word)
    return tuple(
        marker_suffixes[index]
        + "".join(word.text for word in sorted(group, key=lambda value: (value.bbox[1], value.bbox[0])))
        for index, group in enumerate(parts)
    )
