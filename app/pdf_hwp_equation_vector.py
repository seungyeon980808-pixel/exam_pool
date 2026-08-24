"""Geometry-bound vector accents encoded as a bar/head glyph pair."""
from __future__ import annotations

from dataclasses import replace

from .pdf_hwp_equation_decode import formula_prefix
from .pdf_hwp_equation_glyphs import FRACTION_BAR, VECTOR_HEAD
from .pdf_hwp_equation_types import EquationDecoder, EquationWord


def bind_vector_accents(
    words: list[EquationWord], decoded: list[EquationWord], decoder: EquationDecoder,
) -> set[int]:
    """Bind a measured E06D/E06E arrow only to one formula directly below it."""
    consumed: set[int] = set()
    for accent_index, accent in enumerate(words):
        if accent.raw != FRACTION_BAR + VECTOR_HEAD:
            continue
        candidates = [
            index for index, word in enumerate(words)
            if index != accent_index
            and accent.bbox[0] - 2 <= word.bbox[0] <= accent.bbox[2] + 2
            and 2 <= word.bbox[1] - accent.bbox[1] <= 12
            and formula_prefix(
                word.raw, decoder, word.subscript_indices, word.superscript_indices,
            )[0]
        ]
        if len(candidates) != 1:
            decoder.unknown.add(f"ambiguous-vector@{accent.bbox[0]:.2f},{accent.bbox[1]:.2f}")
            continue
        target_index = candidates[0]
        formula, tail = formula_prefix(
            words[target_index].raw, decoder,
            words[target_index].subscript_indices, words[target_index].superscript_indices,
        )
        decoded[target_index] = replace(
            decoded[target_index], text=f"[[formula:\\vec{{{formula}}}]]{tail}",
        )
        decoded[accent_index] = replace(decoded[accent_index], suppressed=True)
        consumed.add(accent_index)
    return consumed


__all__ = ["bind_vector_accents"]
