"""Geometry-backed binding for equation scripts split into separate PDF words."""
from __future__ import annotations

from dataclasses import replace

from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN
from .pdf_hwp_equation_types import EquationDecoder, EquationWord, FORMULA_PREFIX_RE


def bind_detached_superscripts(
    words: list[EquationWord], decoder: EquationDecoder,
) -> list[EquationWord]:
    """Bind a uniquely stacked superscript above an existing trailing subscript."""
    result = list(words)
    claims: dict[int, list[int]] = {}
    ambiguous: set[int] = set()
    for candidate_index, candidate in enumerate(words):
        box = candidate.char_bboxes[0] if len(candidate.char_bboxes) == 1 else None
        if (
            len(candidate.raw) != 1 or candidate.subscript_indices or candidate.superscript_indices
            or box is None or candidate.raw in {FRACTION_BAR, RADICAL_SIGN}
            or FORMULA_PREFIX_RE.fullmatch(candidate.raw) is None
            or not decoder.run(candidate.raw).isdigit()
        ):
            continue
        plausible_targets: list[int] = []
        safe_targets: list[int] = []
        for word_index, word in enumerate(words):
            if (
                word_index == candidate_index or not word.subscript_indices
                or word.subscript_indices[-1] != len(word.raw) - 1 or not word.char_bboxes
            ):
                continue
            subscripted = frozenset(word.subscript_indices)
            subscript_start = len(word.raw) - 1
            while subscript_start - 1 in subscripted:
                subscript_start -= 1
            if subscript_start == 0:
                continue
            base_box = word.char_bboxes[subscript_start - 1]
            subscript_box = word.char_bboxes[subscript_start]
            if base_box is None or subscript_box is None:
                continue
            base_height = base_box[3] - base_box[1]
            if not base_height:
                continue
            height_ratio = (box[3] - box[1]) / base_height
            center_delta = (box[1] + box[3] - base_box[1] - base_box[3]) / 2
            x_gap = box[0] - base_box[2]
            aligned = abs((box[0] + box[2] - subscript_box[0] - subscript_box[2]) / 2) <= 1
            stack_geometry = -6.5 <= center_delta <= -2 and 0 <= x_gap <= 2 and aligned
            if stack_geometry and 0.55 <= height_ratio <= 0.78:
                plausible_targets.append(word_index)
                if 0.60 <= height_ratio <= 0.72:
                    safe_targets.append(word_index)
        if len(plausible_targets) != 1 or safe_targets != plausible_targets:
            if plausible_targets:
                ambiguous.add(candidate_index)
            continue
        claims.setdefault(safe_targets[0], []).append(candidate_index)
    bindings: dict[int, int] = {}
    for word_index, candidates in claims.items():
        if len(candidates) == 1:
            bindings[candidates[0]] = word_index
        else:
            ambiguous.update(candidates)
    for candidate_index in ambiguous:
        result[candidate_index] = replace(result[candidate_index], ambiguous_superscript=True)
    for candidate_index, word_index in bindings.items():
        if candidate_index in ambiguous:
            continue
        word = result[word_index]
        candidate = words[candidate_index]
        offset = len(word.raw)
        result[word_index] = replace(
            word,
            bbox=(
                min(word.bbox[0], candidate.bbox[0]), min(word.bbox[1], candidate.bbox[1]),
                max(word.bbox[2], candidate.bbox[2]), max(word.bbox[3], candidate.bbox[3]),
            ),
            raw=word.raw + candidate.raw,
            superscript_indices=(*word.superscript_indices, offset),
            char_bboxes=(*word.char_bboxes, *candidate.char_bboxes),
        )
    return [word for index, word in enumerate(result) if index not in bindings]
