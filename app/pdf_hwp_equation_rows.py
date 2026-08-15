"""Assign normalized equation words to source text rows by measured geometry."""
from __future__ import annotations

import re

from .pdf_hwp_equation_types import EquationDecoder, EquationWord


_STATEMENT_MARKER_RE = re.compile(r"^[ㄱㄴㄷ]\.$")
_ROW_Y0_TOLERANCE = 15.5


def _vertical_overlap(left: EquationWord, right: EquationWord) -> float:
    return max(0.0, min(left.bbox[3], right.bbox[3]) - max(left.bbox[1], right.bbox[1]))


def _greedy_rows(words: list[EquationWord]) -> list[list[EquationWord]]:
    grouped: list[list[EquationWord]] = []
    ordered = sorted(
        (word for word in words if not word.suppressed),
        key=lambda value: (value.bbox[1], value.bbox[0]),
    )
    for word in ordered:
        if not grouped or abs(word.bbox[1] - grouped[-1][0].bbox[1]) > _ROW_Y0_TOLERANCE:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    return grouped


def group_rows(
    words: list[EquationWord], decoder: EquationDecoder | None = None,
) -> list[list[EquationWord]]:
    """Group rows, then bind statement formulas to the unique overlapping ㄱ/ㄴ/ㄷ marker."""
    grouped = _greedy_rows(words)
    markers = tuple(
        (row_index, word)
        for row_index, row in enumerate(grouped)
        for word in row
        if _STATEMENT_MARKER_RE.fullmatch(word.text)
    )
    marker_rows = {row_index for row_index, _marker in markers}
    targets: dict[int, int] = {}
    for source_index, row in enumerate(grouped):
        if source_index not in marker_rows:
            continue
        for word in row:
            if not word.text.startswith("[[formula:"):
                continue
            candidates = tuple(sorted({
                row_index
                for row_index, marker in markers
                if _vertical_overlap(word, marker) > 0.0
            }))
            if len(candidates) > 1:
                if decoder is not None:
                    decoder.unknown.add(
                        f"ambiguous-equation-row@{word.bbox[0]:.2f},{word.bbox[1]:.2f}"
                    )
                continue
            if candidates:
                targets[id(word)] = candidates[0]

    reassigned: list[list[EquationWord]] = [[] for _row in grouped]
    for source_index, row in enumerate(grouped):
        for word in row:
            reassigned[targets.get(id(word), source_index)].append(word)
    return [
        sorted(row, key=lambda value: value.bbox[0])
        for row in reassigned
        if row
    ]
