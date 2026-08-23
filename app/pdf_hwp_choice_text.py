"""Read five KICE text choices independently of their row/column layout."""
from __future__ import annotations

import re
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
        return centers[0] - 16 <= value <= centers[0] + 24
    gap = median(right - left for left, right in zip(centers, centers[1:]))
    return centers[0] - gap / 2 <= value <= centers[-1] + max(gap / 2, 24)


def _stacked_fraction(
    marker: ChoiceWord,
    group: list[ChoiceWord],
    radical_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[str, tuple[ChoiceWord, ...]] | None:
    upper = tuple(
        word for word in group
        if word.bbox[1] < marker.bbox[1] - 3 and word.text.strip().isdigit()
    )
    lower = tuple(
        word for word in group
        if word.bbox[1] > marker.bbox[1] + 3 and word.text.strip().isdigit()
    )
    if not upper or len(lower) != 1:
        return None
    denominator = lower[0]
    aligned = tuple(
        word for word in upper
        if abs((word.bbox[0] + word.bbox[2]) / 2 -
               (denominator.bbox[0] + denominator.bbox[2]) / 2) < 18
    )
    if not aligned:
        return None
    numerator_parts: list[str] = []
    for word in sorted(aligned, key=lambda value: value.bbox[0]):
        center_x = (word.bbox[0] + word.bbox[2]) / 2
        radical = any(
            x0 <= center_x <= x1 and y0 < marker.bbox[1] and y1 > word.bbox[1]
            for x0, y0, x1, y1 in radical_bboxes
        )
        token = word.text.strip()
        numerator_parts.append(f"\\sqrt{{{token}}}" if radical else token)
    formula = f"[[formula:\\frac{{{''.join(numerator_parts)}}}{{{denominator.text.strip()}}}]]"
    return formula, (*aligned, denominator)


def _ordered_group(
    group: list[ChoiceWord],
    fraction: tuple[str, tuple[ChoiceWord, ...]] | None,
) -> tuple[str, ...]:
    if fraction is None:
        row_centers = _clusters(tuple(word.bbox[1] for word in group), 8.0)
        return tuple(
            word.text.strip()
            for word in sorted(
                group,
                key=lambda value: (
                    _nearest(value.bbox[1], row_centers), value.bbox[0],
                ),
            )
            if word.text.strip()
        )
    used = fraction[1] if fraction else ()
    values = [
        (word.bbox[0], word.text.strip())
        for word in group if word not in used
    ]
    if fraction:
        values.append((min(word.bbox[0] for word in used), fraction[0]))
    return tuple(value for _, value in sorted(values) if value)


def choice_texts(
    words: list[ChoiceWord],
    start_y: float,
    radical_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[str, ...]:
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
    marker_rows = {
        row_index: sorted(
            ((word.bbox[0], index) for index, word in enumerate(markers)
             if _nearest(word.bbox[1], row_centers) == row_index),
        )
        for row_index in range(len(row_centers))
    }
    marker_prefixes = tuple(
        word.text.split(marker, 1)[0].strip() for marker, word in zip(CIRCLED, markers)
    )
    marker_suffixes = tuple(word.text.split(marker, 1)[1] for marker, word in zip(CIRCLED, markers))
    parts: list[list[ChoiceWord]] = [[] for _ in CIRCLED]
    for word in visible:
        if word in markers or not _within_outer_row(word.bbox[1], row_centers):
            continue
        row_index = _nearest(word.bbox[1], row_centers)
        preceding = [
            marker_index for marker_x, marker_index in marker_rows[row_index]
            if marker_x <= word.bbox[0]
        ]
        if not preceding:
            continue
        marker_index = preceding[-1]
        marker_word = markers[marker_index]
        same_line = abs(word.bbox[1] - marker_word.bbox[1]) <= 8
        if same_line and word.bbox[0] < marker_word.bbox[2] - 1:
            continue
        parts[marker_index].append(word)
    glued_prefixes = tuple(
        marker_prefixes[index + 1]
        if index + 1 < len(marker_prefixes)
        and not marker_suffixes[index].strip()
        and marker_prefixes[index + 1].startswith("[[formula:")
        and _nearest(markers[index].bbox[1], row_centers)
        == _nearest(markers[index + 1].bbox[1], row_centers)
        else ""
        for index in range(len(parts))
    )
    fractions = tuple(
        _stacked_fraction(marker, group, radical_bboxes)
        for marker, group in zip(markers, parts, strict=True)
    )
    choices = tuple(
        " ".join(
            value for value in (
                marker_suffixes[index].strip(),
                glued_prefixes[index],
                *_ordered_group(group, fractions[index]),
            ) if value
        )
        for index, group in enumerate(parts)
    )
    # Keep combined-answer choices atomic in the narrow five-choice row.  The
    # semantic tokens remain unchanged; only optional post-comma display spaces
    # are removed so the final ㄷ cannot wrap into another column.
    normalized: list[str] = []
    for choice in choices:
        if choice.count(",") >= 2 and all(label in choice for label in "ㄱㄴㄷ"):
            # Page-edge subject headers can geometrically overlap the last
            # horizontal choice.  A three-claim combination has no prose of
            # its own, so retain only its semantic claim labels.
            normalized.append("ㄱ,ㄴ,ㄷ")
        else:
            normalized.append(re.sub(r"(?<=[ㄱㄴㄷ]),\s+(?=[ㄱㄴㄷ])", ",", choice))
    return tuple(normalized)
