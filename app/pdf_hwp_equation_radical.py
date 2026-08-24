"""Measured structural-bar scoping for radicals embedded in PDF words."""
from __future__ import annotations

from dataclasses import replace
import re

from .pdf_hwp_equation_decode import formula_prefix, word_slice
from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN
from .pdf_hwp_equation_types import EquationDecoder, EquationWord, FORMULA_PREFIX_RE


def bind_drawn_inline_radicals(
    words: list[EquationWord],
    radical_bboxes: tuple[tuple[float, float, float, float], ...],
) -> list[EquationWord]:
    """Recover a numeric radical whose hook/vinculum is vector ink."""
    result = list(words)
    unique: list[tuple[float, float, float, float]] = []
    for bbox in radical_bboxes:
        if any(
            max(abs(left - right) for left, right in zip(bbox, seen)) <= 1.0
            for seen in unique
        ):
            continue
        unique.append(bbox)
    for x0, y0, x1, y1 in unique:
        radicands = [
            index for index, word in enumerate(result)
            if not word.suppressed and word.text.strip().isdigit()
            and x0 + 4 <= (word.bbox[0] + word.bbox[2]) / 2 <= x1 + 1
            and y0 - 3 <= (word.bbox[1] + word.bbox[3]) / 2 <= y1 + 4
        ]
        if len(radicands) != 1:
            continue
        radicand_index = radicands[0]
        radicand = result[radicand_index]
        preceding = [
            index for index, word in enumerate(result)
            if index != radicand_index and not word.suppressed
            and 0 <= radicand.bbox[0] - word.bbox[2] <= 12
            and abs(word.bbox[1] - radicand.bbox[1]) <= 3
            and re.search(r"\d+$", word.text.strip())
        ]
        if len(preceding) != 1:
            continue
        coefficient_index = preceding[0]
        coefficient_word = result[coefficient_index]
        match = re.search(r"(\d+)$", coefficient_word.text.strip())
        if match is None:
            continue
        prefix = coefficient_word.text[: coefficient_word.text.rfind(match.group(1))]
        formula = f"[[formula:{match.group(1)}\\sqrt{{{radicand.text.strip()}}}]]"
        result[coefficient_index] = replace(coefficient_word, text=prefix + formula)
        result[radicand_index] = replace(radicand, suppressed=True)
    return result


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
    prefix_word = word_slice(word, 0, radical_index)
    prefix, prefix_tail = formula_prefix(
        prefix_word.raw, decoder, prefix_word.subscript_indices,
        prefix_word.superscript_indices,
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
    return replace(
        word,
        text=f"{prefix_tail}[[formula:{prefix}\\sqrt{{{radicand}}}{suffix}]]{tail}",
    )


def bind_detached_inline_radicands(words: list[EquationWord]) -> list[EquationWord]:
    """Merge a uniquely bar-covered radicand split into the following PDF word."""
    result = list(words)
    for radical_index, radical in enumerate(tuple(result)):
        sign_index = radical.raw.find(RADICAL_SIGN)
        bar_index = sign_index + 1
        if (
            sign_index < 0 or bar_index >= len(radical.raw)
            or radical.raw[bar_index] != FRACTION_BAR
            or bar_index + 1 != len(radical.raw)
        ):
            continue
        bar_bbox = radical.char_bboxes[bar_index] if radical.char_bboxes else None
        if bar_bbox is None:
            continue
        candidates: list[int] = []
        for candidate_index, candidate in enumerate(result):
            if candidate_index == radical_index or abs(candidate.bbox[1] - radical.bbox[1]) > 10:
                continue
            run = 0
            for char, bbox in zip(candidate.raw, candidate.char_bboxes, strict=True):
                if FORMULA_PREFIX_RE.fullmatch(char) is None or bbox is None:
                    break
                center = (bbox[0] + bbox[2]) / 2
                if center < bar_bbox[0] - 1 or center > bar_bbox[2] + 1:
                    break
                run += 1
            if run:
                candidates.append(candidate_index)
        if len(candidates) != 1:
            continue
        candidate_index = candidates[0]
        radicand = result[candidate_index]
        offset = len(radical.raw)
        result[radical_index] = replace(
            radical,
            bbox=tuple(float(value) for value in (
                min(radical.bbox[0], radicand.bbox[0]), min(radical.bbox[1], radicand.bbox[1]),
                max(radical.bbox[2], radicand.bbox[2]), max(radical.bbox[3], radicand.bbox[3]),
            )),
            raw=radical.raw + radicand.raw,
            subscript_indices=(*radical.subscript_indices, *(
                offset + index for index in radicand.subscript_indices
            )),
            superscript_indices=(*radical.superscript_indices, *(
                offset + index for index in radicand.superscript_indices
            )),
            char_bboxes=(*radical.char_bboxes, *radicand.char_bboxes),
        )
        result[candidate_index] = replace(radicand, raw="")
    return [word for word in result if word.raw]
