"""Detect question regions in text-layer EBS 수능특강 textbooks."""
from __future__ import annotations

import re
from typing import Final

import fitz

from .pdf_hwp_pipeline_models import DetectedItem


_QUESTION_NUMBER: Final = re.compile(r"^(?:0[1-9]|[12]\d)$")
_SOURCE_ID: Final = re.compile(r"\[26023-(\d{4})\]")
_COLUMN_TOLERANCE: Final = 40.0
_MIN_MARK_HEIGHT: Final = 15.0
_BOTTOM_MARGIN: Final = 48.0


def detect_ebs_textbook_items(document: fitz.Document) -> tuple[DetectedItem, ...] | None:
    """Return EBS question boxes, or ``None`` when the source is not an EBS textbook."""
    identity = " ".join(document[index].get_text() for index in range(min(4, document.page_count)))
    if "EBS 수능특강" not in identity:
        return None

    found: list[DetectedItem] = []
    answer_section = False
    for page_index, page in enumerate(document):
        page_text = page.get_text().strip()
        compact_text = "".join(page_text.split())
        if page_index > document.page_count // 2 and len(page_text) < 100 and "정답과해설" in compact_text:
            answer_section = True
        if answer_section:
            continue
        marks = [
            (int(str(word[4])), float(word[0]), float(word[1]))
            for word in page.get_text("words")
            if _QUESTION_NUMBER.fullmatch(str(word[4]).strip())
            and float(word[3]) - float(word[1]) >= _MIN_MARK_HEIGHT
            and float(word[2]) - float(word[0]) >= 10
            and float(word[1]) < page.rect.height - 60
        ]
        if len(marks) < 2:
            continue
        starts: list[list[float]] = []
        for x in sorted(mark[1] for mark in marks):
            if not starts or x - starts[-1][-1] > _COLUMN_TOLERANCE:
                starts.append([x])
            else:
                starts[-1].append(x)
        column_starts = tuple(min(group) for group in starts)
        by_column: list[list[tuple[int, float, float]]] = [[] for _ in column_starts]
        for mark in marks:
            column = min(
                range(len(column_starts)),
                key=lambda index: abs(mark[1] - column_starts[index]),
            )
            if abs(mark[1] - column_starts[column]) <= _COLUMN_TOLERANCE / 2:
                by_column[column].append(mark)
        for column, column_marks in enumerate(by_column):
            right = (
                column_starts[column + 1] - 8
                if column + 1 < len(column_starts)
                else page.rect.width - 8
            )
            ordered = sorted(column_marks, key=lambda mark: mark[2])
            for mark_index, (number, x, y) in enumerate(ordered):
                bottom = (
                    ordered[mark_index + 1][2] - 8
                    if mark_index + 1 < len(ordered)
                    else page.rect.height - _BOTTOM_MARGIN
                )
                bbox = (
                    max(0.0, x - 8),
                    max(0.0, y - 8),
                    min(float(page.rect.width), right),
                    min(float(page.rect.height), bottom),
                )
                source_text = page.get_text(clip=fitz.Rect(bbox)).strip()
                source_id = _SOURCE_ID.search(source_text)
                found.append(DetectedItem(
                    page_number=page_index + 1,
                    # The printed 01–29 number restarts in every section.  The
                    # source identifier is monotonic and therefore safe for DB
                    # selection, progress, manifests, and full-book output.
                    item_number=int(source_id.group(1)) if source_id else number,
                    column=column,
                    bbox=tuple(round(value, 3) for value in bbox),
                    source_text=source_text,
                ))
    return tuple(found)
