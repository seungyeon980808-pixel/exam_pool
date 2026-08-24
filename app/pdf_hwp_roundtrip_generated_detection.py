"""Detect leading question numbers in generated round-trip PDFs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Final

import fitz

from .pdf_hwp_pipeline_models import DetectionResult, DetectedItem, InvalidSourcePdfError


_LEADING_NUMBER: Final = re.compile(r"^([1-9]\d{0,3})\.$")
_LEADING_PREFIX: Final = re.compile(r"^([1-9]\d{0,3})\.(?=\D)")
_COLUMN_TOLERANCE: Final = 40.0
_MARGIN: Final = 8.0
_TOP_MARGIN: Final = 20.0
_BOTTOM_MARGIN: Final = 60.0
_MIN_ITEM_HEIGHT: Final = 60.0
_BASELINE_TOLERANCE: Final = 1.0


@dataclass(frozen=True, slots=True)
class _NumberMark:
    item_number: int
    x: float
    y: float


def _split_block_marks(words: tuple[tuple, ...]) -> tuple[_NumberMark, ...]:
    blocks: dict[int, list[tuple]] = {}
    for word in words:
        blocks.setdefault(int(word[5]), []).append(word)
    marks: list[_NumberMark] = []
    for block in blocks.values():
        ordered = sorted(block, key=lambda word: (int(word[6]), int(word[7])))
        prefix: list[str] = []
        for word in ordered:
            token = str(word[4]).strip()
            if (token.isdigit() or token == ".") and (
                not prefix or abs(float(word[1]) - float(ordered[0][1])) <= _BASELINE_TOLERANCE
            ):
                prefix.append(token)
                if token == ".":
                    break
            else:
                break
        match = _LEADING_NUMBER.fullmatch("".join(prefix))
        if match is not None and len(prefix) > 1:
            marks.append(_NumberMark(
                int(match.group(1)), float(ordered[0][0]), float(ordered[0][1]),
            ))
    return tuple(marks)


def _page_marks(page: fitz.Page) -> tuple[_NumberMark, ...]:
    words = tuple(page.get_text("words"))
    marks = list(_split_block_marks(words))
    for word in words:
        token = str(word[4]).strip()
        match = _LEADING_NUMBER.fullmatch(token) or _LEADING_PREFIX.match(token)
        if (
            match is not None
            and int(word[7]) == 0
            and float(word[1]) >= _TOP_MARGIN
            and float(word[3]) <= page.rect.height - _BOTTOM_MARGIN
        ):
            marks.append(_NumberMark(int(match.group(1)), float(word[0]), float(word[1])))
    return tuple(
        mark for mark in marks
        if mark.y >= _TOP_MARGIN and mark.y <= page.rect.height - _BOTTOM_MARGIN
    )


def _aligned_marks(page_marks: tuple[tuple[_NumberMark, ...], ...]) -> tuple[tuple[_NumberMark, ...], ...]:
    all_marks = tuple(mark for marks in page_marks for mark in marks)
    if len(all_marks) <= 1:
        return page_marks
    return tuple(tuple(
        mark for mark in marks
        if any(peer is not mark and abs(peer.x - mark.x) <= _COLUMN_TOLERANCE for peer in all_marks)
    ) for marks in page_marks)


def _column_groups(marks: tuple[_NumberMark, ...]) -> tuple[tuple[_NumberMark, ...], ...]:
    groups: list[list[_NumberMark]] = []
    for mark in sorted(marks, key=lambda candidate: (candidate.x, candidate.y)):
        if not groups or mark.x - groups[-1][-1].x > _COLUMN_TOLERANCE:
            groups.append([mark])
        else:
            groups[-1].append(mark)
    return tuple(tuple(sorted(group, key=lambda candidate: candidate.y)) for group in groups)


def _page_items(
    page: fitz.Page,
    marks: tuple[_NumberMark, ...],
    next_page: fitz.Page | None = None,
    next_marks: tuple[_NumberMark, ...] = (),
) -> tuple[DetectedItem, ...]:
    columns = _column_groups(marks)
    items: list[DetectedItem] = []
    body_top = min((mark.y for mark in marks), default=_TOP_MARGIN)
    for column, marks in enumerate(columns):
        right = columns[column + 1][0].x - _MARGIN if column + 1 < len(columns) else page.rect.width - _MARGIN
        for index, mark in enumerate(marks):
            bottom = marks[index + 1].y - _MARGIN if index + 1 < len(marks) else page.rect.height - _BOTTOM_MARGIN
            bbox = (
                max(0.0, mark.x - _MARGIN),
                max(0.0, mark.y - _MARGIN),
                min(float(page.rect.width), right),
                min(float(page.rect.height), bottom),
            )
            if bbox[3] - bbox[1] < _MIN_ITEM_HEIGHT:
                continue
            source_parts = [page.get_text(clip=fitz.Rect(bbox)).strip()]
            if index + 1 == len(marks) and column + 1 < len(columns):
                next_column = columns[column + 1]
                continuation_right = (
                    columns[column + 2][0].x - _MARGIN
                    if column + 2 < len(columns) else page.rect.width - _MARGIN
                )
                continuation = fitz.Rect(
                    next_column[0].x - _MARGIN, body_top - _MARGIN,
                    continuation_right, next_column[0].y - _MARGIN,
                )
                if continuation.height > 0:
                    source_parts.append(page.get_text(clip=continuation).strip())
            if (
                index + 1 == len(marks)
                and column + 1 == len(columns)
                and next_page is not None
                and next_marks
            ):
                next_columns = _column_groups(next_marks)
                first_column = next_columns[0]
                next_body_top = min(mark.y for mark in next_marks)
                continuation_right = (
                    next_columns[1][0].x - _MARGIN
                    if len(next_columns) > 1 else next_page.rect.width - _MARGIN
                )
                continuation = fitz.Rect(
                    first_column[0].x - _MARGIN, next_body_top - _MARGIN,
                    continuation_right, first_column[0].y - _MARGIN,
                )
                if continuation.height > 0:
                    source_parts.append(next_page.get_text(clip=continuation).strip())
            items.append(DetectedItem(
                page_number=page.number + 1,
                item_number=mark.item_number,
                column=column,
                bbox=tuple(round(value, 3) for value in bbox),
                source_text="\n".join(part for part in source_parts if part),
            ))
    return tuple(items)


def detect_generated_items(generated_pdf: Path) -> DetectionResult:
    """Detect generated question leaders without changing source-PDF grammar."""
    source = generated_pdf.resolve()
    try:
        document = fitz.open(source)
    except (fitz.FileDataError, FileNotFoundError, OSError) as exc:
        raise InvalidSourcePdfError(source_pdf=source, detail=str(exc)) from exc
    with document:
        marks_by_page = _aligned_marks(tuple(_page_marks(page) for page in document))
        items = tuple(
            item
            for index, (page, marks) in enumerate(zip(document, marks_by_page, strict=True))
            for item in _page_items(
                page, marks,
                document[index + 1] if index + 1 < document.page_count else None,
                marks_by_page[index + 1] if index + 1 < document.page_count else (),
            )
        )
        page_count = document.page_count
    return DetectionResult(
        source_pdf=source,
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        page_count=page_count,
        items=items,
    )
