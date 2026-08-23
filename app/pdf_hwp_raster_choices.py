"""Recover editable answer choices from positioned raster OCR words."""
from __future__ import annotations

import re

from .pdf_hwp_raster_ocr import RasterOcrWord


CIRCLED_CHOICE_MARKERS = "①②③④⑤"


def choice_ordinal(word: RasterOcrWord) -> int | None:
    text = word.text.strip()
    for index, marker in enumerate(CIRCLED_CHOICE_MARKERS, 1):
        if marker in text:
            return index
    match = re.fullmatch(r"([1-5])[.)]?", text)
    return int(match.group(1)) if match else None


def choice_markers(words: tuple[RasterOcrWord, ...]) -> tuple[RasterOcrWord, ...]:
    """Recover a choice row even when OCR reads a circled digit as a plain digit."""
    candidates = tuple(word for word in words if choice_ordinal(word) is not None)
    rows: list[tuple[int, float, tuple[RasterOcrWord, ...]]] = []
    for anchor in candidates:
        anchor_center = (anchor.bbox[1] + anchor.bbox[3]) / 2
        same_row = tuple(
            word for word in candidates
            if abs((word.bbox[1] + word.bbox[3]) / 2 - anchor_center) <= 5
        )
        selected: list[RasterOcrWord] = []
        for ordinal in range(1, 6):
            matches = [word for word in same_row if choice_ordinal(word) == ordinal]
            if not matches:
                break
            circled = [
                word for word in matches
                if any(marker in word.text for marker in CIRCLED_CHOICE_MARKERS)
            ]
            selected.append(min(circled or matches, key=lambda word: word.bbox[0]))
        if len(selected) != 5:
            continue
        selected.sort(key=lambda word: word.bbox[0])
        if [choice_ordinal(word) for word in selected] != list(range(1, 6)):
            continue
        explicit_count = sum(
            any(marker in word.text for marker in CIRCLED_CHOICE_MARKERS)
            for word in selected
        )
        rows.append((explicit_count, anchor_center, tuple(selected)))
    return max(rows, key=lambda value: (value[0], value[1]))[2] if rows else ()


def restore_bogi_choice_glyphs(
    choices: tuple[str, ...], claim_text: str,
) -> tuple[str, ...]:
    """Restore ㄱ/ㄴ only when the full choice set proves a bogi combination."""
    if not all(marker in claim_text for marker in "ㄱㄴㄷ"):
        return choices
    compact = tuple(re.sub(r"\s", "", choice) for choice in choices)
    if not any("7" in choice or "L" in choice for choice in compact):
        return choices
    if not all(
        re.fullmatch(r"[ㄱㄴㄷ7L](?:,[ㄱㄴㄷ7L])*", choice)
        for choice in compact
    ):
        return choices
    restored = tuple(
        choice.translate(str.maketrans({"7": "ㄱ", "L": "ㄴ"}))
        for choice in compact
    )
    if len(set(restored)) != len(restored):
        return choices
    return tuple(choice.replace(",", ", ") for choice in restored)
