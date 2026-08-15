"""Typed numerator extent recovery for stacked PDF fractions."""
from __future__ import annotations

from dataclasses import dataclass

from .pdf_hwp_equation_decode import formula_run, word_slice
from .pdf_hwp_equation_types import EquationDecoder, EquationWord, FORMULA_PREFIX_RE


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
        if bar.bbox[0] - 3 <= center <= bar.bbox[2] + 3:
            covered = index + 1
        elif covered:
            break
    if not covered:
        return AmbiguousFractionNumerator(word.bbox[0], word.bbox[1])
    numerator = formula_run(word_slice(word, 0, covered), decoder)
    suffix = formula_run(word_slice(word, covered, match.end()), decoder)
    return FractionNumerator(numerator, suffix, word.raw[match.end():])
