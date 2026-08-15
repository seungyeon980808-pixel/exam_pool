"""Measured structural-bar scoping for radicals embedded in PDF words."""
from __future__ import annotations

from dataclasses import replace

from .pdf_hwp_equation_decode import decode_formula, formula_prefix, word_slice
from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN
from .pdf_hwp_equation_types import EquationDecoder, EquationWord, FORMULA_PREFIX_RE


def _ambiguous(word: EquationWord, decoder: EquationDecoder) -> EquationWord:
    decoder.unknown.add(f"ambiguous-radical@{word.bbox[0]:.2f},{word.bbox[1]:.2f}")
    return replace(word, text=word.raw)


def decode_inline_radical(
    word: EquationWord, decoder: EquationDecoder,
) -> EquationWord:
    """Decode a radical using its E06D glyph bounds as the radicand boundary."""
    radical_index = word.raw.index(RADICAL_SIGN)
    if radical_index + 1 >= len(word.raw) or word.raw[radical_index + 1] != FRACTION_BAR:
        return _ambiguous(word, decoder)
    prefix = decode_formula(
        word.raw[:radical_index],
        tuple(index for index in word.subscript_indices if index < radical_index),
        decoder,
        tuple(index for index in word.superscript_indices if index < radical_index),
    )
    bar_bbox = (
        word.char_bboxes[radical_index + 1]
        if radical_index + 1 < len(word.char_bboxes) else None
    )
    if bar_bbox is None:
        return _ambiguous(word, decoder)
    radicand_start, radicand_end = radical_index + 2, radical_index + 2
    while radicand_end < len(word.raw):
        if FORMULA_PREFIX_RE.fullmatch(word.raw[radicand_end]) is None:
            break
        bbox = word.char_bboxes[radicand_end] if radicand_end < len(word.char_bboxes) else None
        if bbox is None:
            return _ambiguous(word, decoder)
        center = (bbox[0] + bbox[2]) / 2
        if center < bar_bbox[0] - 1 or abs(center - bar_bbox[2]) <= 1:
            return _ambiguous(word, decoder)
        if center > bar_bbox[2] + 1:
            break
        radicand_end += 1
    if radicand_end == radicand_start:
        return _ambiguous(word, decoder)
    radicand_word = word_slice(word, radicand_start, radicand_end)
    radicand, _ = formula_prefix(
        radicand_word.raw, decoder, radicand_word.subscript_indices,
        radicand_word.superscript_indices,
    )
    if not radicand:
        return _ambiguous(word, decoder)
    suffix_word = word_slice(word, radicand_end)
    suffix, tail = formula_prefix(
        suffix_word.raw, decoder, suffix_word.subscript_indices,
        suffix_word.superscript_indices,
    )
    return replace(word, text=f"[[formula:{prefix}\\sqrt{{{radicand}}}{suffix}]]{tail}")
