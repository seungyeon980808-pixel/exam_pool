"""Typed PDF word and glyph extraction for editable equations."""
from __future__ import annotations

from dataclasses import dataclass
import re

import fitz

from .pdf_hwp_equation_font import EquationFontContext
from .pdf_hwp_equation_glyphs import (
    FRACTION_BAR, VERIFIED_EQUATION_FONT, VERIFIED_PUA_GLYPHS,
)


PUA_RE = re.compile(r"[\ue000-\uf8ff]+")
FORMULA_PREFIX_RE = re.compile(r"^[A-Za-z0-9_+\-=/\.\ue000-\uf8ff]+")


@dataclass(frozen=True, slots=True)
class EquationWord:
    bbox: tuple[float, float, float, float]
    raw: str
    text: str
    suppressed: bool = False
    subscript_indices: tuple[int, ...] = ()
    ambiguous_subscript: bool = False
    superscript_indices: tuple[int, ...] = ()
    ambiguous_superscript: bool = False
    char_bboxes: tuple[tuple[float, float, float, float] | None, ...] = ()


@dataclass(frozen=True, slots=True)
class EquationGlyph:
    raw: str
    bbox: tuple[float, float, float, float]
    is_subscript: bool
    is_ambiguous: bool
    is_superscript: bool = False
    is_ambiguous_superscript: bool = False


class EquationDecoder:
    def __init__(self, context: EquationFontContext | frozenset[str]) -> None:
        self._verified_codepoints = (
            context.verified_codepoints
            if isinstance(context, EquationFontContext)
            else frozenset()
        )
        self.unknown: set[str] = (
            {
                rejection for rejection in context.rejections
                if rejection.startswith((
                    "ambiguous-font-resource-xref:",
                    "embedded-font-unreadable:",
                    "font-occurrence-xref-",
                ))
            }
            if isinstance(context, EquationFontContext)
            else set()
        )

    def char(self, raw: str) -> str:
        if not 0xE000 <= ord(raw) <= 0xF8FF:
            return raw
        verified = (
            VERIFIED_PUA_GLYPHS.get(raw)
            if ord(raw) in self._verified_codepoints
            else None
        )
        if verified is not None:
            return verified
        self.unknown.add(f"U+{ord(raw):04X}")
        return raw

    def run(self, raw: str) -> str:
        return "".join(self.char(char) for char in raw)


def pua_font_names(
    page: fitz.Page, clip: tuple[float, float, float, float],
) -> frozenset[str]:
    names: set[str] = set()
    for block in page.get_text("dict", clip=fitz.Rect(clip))["blocks"]:
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if PUA_RE.search(str(span.get("text", ""))):
                    names.add(str(span.get("font", "")))
    return frozenset(names)


def _script_glyphs(
    page: fitz.Page, clip: tuple[float, float, float, float],
) -> tuple[EquationGlyph, ...]:
    glyphs: list[EquationGlyph] = []
    for block in page.get_text("rawdict", clip=fitz.Rect(clip))["blocks"]:
        for line in block.get("lines", ()):
            previous = None
            for span in line.get("spans", ()):
                chars = tuple(span.get("chars", ()))
                text = "".join(str(char.get("c", "")) for char in chars).strip()
                font = str(span.get("font", ""))
                size = float(span.get("size", 0.0))
                baseline = float(chars[0]["origin"][1]) if chars else 0.0
                bbox = tuple(float(value) for value in span.get("bbox", (0, 0, 0, 0)))
                safe = ambiguous = superscript = ambiguous_superscript = False
                if previous is not None and font == previous[0] == VERIFIED_EQUATION_FONT:
                    ratio = size / previous[1] if previous[1] else 0.0
                    delta = baseline - previous[2]
                    attached = previous[3][0] <= bbox[0] <= previous[3][2] + 2.0
                    suffix = bool(text) and FORMULA_PREFIX_RE.fullmatch(text) is not None
                    previous_is_base = bool(previous[4]) and previous[4][-1] != FRACTION_BAR
                    sub_geometry = 1.5 <= delta <= 4.5 and attached and suffix and previous_is_base
                    safe = sub_geometry and 0.60 <= ratio <= 0.72
                    ambiguous = sub_geometry and 0.55 <= ratio <= 0.78 and not safe
                    super_geometry = -6.5 <= delta <= -2.0 and attached and suffix and previous_is_base
                    superscript = super_geometry and 0.60 <= ratio <= 0.72
                    ambiguous_superscript = (
                        super_geometry and 0.55 <= ratio <= 0.78 and not superscript
                    )
                for char in chars:
                    raw = str(char.get("c", ""))
                    if raw and not raw.isspace():
                        glyphs.append(EquationGlyph(
                            raw, tuple(float(value) for value in char["bbox"]),
                            safe, ambiguous, superscript, ambiguous_superscript,
                        ))
                if text:
                    last = next(char for char in reversed(chars) if str(char.get("c", "")).strip())
                    previous = (
                        font, size, float(last["origin"][1]),
                        tuple(float(value) for value in last["bbox"]), text,
                    )
    return tuple(glyphs)


def page_words(
    page: fitz.Page, clip: tuple[float, float, float, float],
) -> list[EquationWord]:
    glyphs = _script_glyphs(page, clip)
    result: list[EquationWord] = []
    for value in page.get_text("words", clip=fitz.Rect(clip), sort=True):
        bbox = tuple(float(coordinate) for coordinate in value[:4])
        raw, bounds = str(value[4]), fitz.Rect(bbox)
        contained = tuple(glyph for glyph in glyphs if bounds.contains(fitz.Point(
            (glyph.bbox[0] + glyph.bbox[2]) / 2,
            (glyph.bbox[1] + glyph.bbox[3]) / 2,
        )))
        subscripts: set[int] = set()
        superscripts: set[int] = set()
        ambiguous = ambiguous_superscript = False
        boxes: list[tuple[float, float, float, float] | None] = [None] * len(raw)
        remaining, previous_x = list(contained), float("-inf")
        for position, char in enumerate(raw):
            candidates = [
                glyph for glyph in remaining
                if glyph.raw == char and (glyph.bbox[0] + glyph.bbox[2]) / 2 >= previous_x - 1
            ]
            if not candidates:
                continue
            glyph = min(candidates, key=lambda item: (item.bbox[0], item.bbox[1]))
            remaining.remove(glyph)
            previous_x = (glyph.bbox[0] + glyph.bbox[2]) / 2
            boxes[position] = glyph.bbox
            ambiguous = ambiguous or glyph.is_ambiguous
            ambiguous_superscript = ambiguous_superscript or glyph.is_ambiguous_superscript
            if glyph.is_subscript:
                subscripts.add(position)
            if glyph.is_superscript:
                superscripts.add(position)
        result.append(EquationWord(
            bbox, raw, raw, subscript_indices=tuple(sorted(subscripts)),
            ambiguous_subscript=ambiguous,
            superscript_indices=tuple(sorted(superscripts)),
            ambiguous_superscript=ambiguous_superscript, char_bboxes=tuple(boxes),
        ))
    return result
