"""Typed numerator extent recovery for stacked PDF fractions."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from .pdf_hwp_equation_decode import decode_word, formula_run, word_slice
from .pdf_hwp_equation_types import EquationDecoder, EquationWord, FORMULA_PREFIX_RE


_FORMULA_TEXT = re.compile(r"\[\[formula:(.+)\]\]`?")
_MIXED_FORMULA_TEXT = re.compile(
    r"(?P<prefix>[A-Za-z0-9]+)\[\[formula:(?P<formula>.+)\]\]`?"
)


def _standalone_formula(word: EquationWord) -> str | None:
    match = _FORMULA_TEXT.fullmatch(word.text)
    if match is not None:
        return match.group(1)
    mixed = _MIXED_FORMULA_TEXT.fullmatch(word.text)
    if mixed is not None:
        return mixed.group("prefix") + mixed.group("formula")
    if re.fullmatch(r"[A-Za-z0-9]+", word.text.strip()):
        return word.text.strip()
    return None


def bind_drawn_fractions(
    words: list[EquationWord],
    bars: tuple[tuple[float, float, float, float], ...],
) -> list[EquationWord]:
    """Bind stacked numerator/denominator text around vector fraction bars."""
    result = list(words)
    consumed: set[int] = set()
    for x0, y0, x1, y1 in sorted(bars, key=lambda value: (value[1], value[0])):
        y = (y0 + y1) / 2
        above = [
            index for index, word in enumerate(result)
            if index not in consumed and not word.suppressed
            and _standalone_formula(word) is not None
            and x0 - 2 <= (word.bbox[0] + word.bbox[2]) / 2 <= x1 + 2
            and 0 <= y - word.bbox[3] <= 14
        ]
        below = [
            index for index, word in enumerate(result)
            if index not in consumed and not word.suppressed
            and _standalone_formula(word) is not None
            and x0 - 2 <= (word.bbox[0] + word.bbox[2]) / 2 <= x1 + 2
            and 0 <= word.bbox[1] - y <= 14
        ]
        if len(above) != 1 or len(below) != 1:
            continue
        upper_index, lower_index = above[0], below[0]
        numerator = _standalone_formula(result[upper_index])
        denominator = _standalone_formula(result[lower_index])
        if numerator is None or denominator is None:
            continue
        suffix_candidates = [
            index for index, word in enumerate(result)
            if index not in consumed and index not in {upper_index, lower_index}
            and not word.suppressed and _standalone_formula(word) is not None
            and 0 <= word.bbox[0] - x1 <= 12
            and abs((word.bbox[1] + word.bbox[3]) / 2 - y) <= 8
        ]
        suffix = ""
        if len(suffix_candidates) == 1:
            suffix_index = suffix_candidates[0]
            suffix = _standalone_formula(result[suffix_index]) or ""
            consumed.add(suffix_index)
            result[suffix_index] = replace(result[suffix_index], suppressed=True)
        result[upper_index] = replace(
            result[upper_index],
            bbox=(
                min(result[upper_index].bbox[0], result[lower_index].bbox[0]),
                result[upper_index].bbox[1],
                max(result[upper_index].bbox[2], result[lower_index].bbox[2]),
                result[lower_index].bbox[3],
            ),
            text=f"[[formula:\\frac{{{numerator}}}{{{denominator}}}{suffix}]]",
        )
        result[lower_index] = replace(result[lower_index], suppressed=True)
        consumed.update((upper_index, lower_index))
    return result


@dataclass(frozen=True, slots=True)
class FractionNumerator:
    numerator: str
    suffix: str
    tail: str


@dataclass(frozen=True, slots=True)
class AmbiguousFractionNumerator:
    x: float
    y: float


def fraction_numerator(
    word: EquationWord, bar: EquationWord, decoder: EquationDecoder,
) -> FractionNumerator | AmbiguousFractionNumerator:
    """Recover only the numerator glyphs whose measured centers overlap the bar."""
    match = FORMULA_PREFIX_RE.match(word.raw)
    if match is None:
        return FractionNumerator("", "", word.raw)
    covered = 0
    for index in range(match.end()):
        bbox = word.char_bboxes[index] if index < len(word.char_bboxes) else None
        if bbox is None:
            return AmbiguousFractionNumerator(word.bbox[0], word.bbox[1])
        center = (bbox[0] + bbox[2]) / 2
        if bar.bbox[0] - 3 <= center <= bar.bbox[2]:
            covered = index + 1
        elif covered:
            break
    if not covered:
        return AmbiguousFractionNumerator(word.bbox[0], word.bbox[1])
    while covered < match.end() and (
        covered in word.subscript_indices or covered in word.superscript_indices
    ):
        covered += 1
    numerator = formula_run(word_slice(word, 0, covered), decoder)
    if word.raw[covered:covered + 1] in {"×", "·"}:
        tail = decode_word(word_slice(word, covered), decoder).text
        return FractionNumerator(numerator, "", tail)
    suffix = formula_run(word_slice(word, covered, match.end()), decoder)
    remainder = word_slice(word, match.end())
    tail = decode_word(remainder, decoder).text if remainder.raw else ""
    return FractionNumerator(numerator, suffix, tail)
