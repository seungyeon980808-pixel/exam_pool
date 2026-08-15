"""Meaning and script decoding for verified equation words."""
from __future__ import annotations

from dataclasses import replace

from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN
from .pdf_hwp_equation_types import (
    EquationDecoder, EquationWord, FORMULA_PREFIX_RE, PUA_RE,
)


def decode_formula(
    raw: str,
    indices: tuple[int, ...],
    decoder: EquationDecoder,
    superscript_indices: tuple[int, ...] = (),
) -> str:
    subscripted, superscripted = frozenset(indices), frozenset(superscript_indices)
    result: list[str] = []
    index = 0
    while index < len(raw):
        marker = "_" if index in subscripted else "^" if index in superscripted else ""
        if not marker:
            result.append(decoder.char(raw[index]))
            index += 1
            continue
        active = subscripted if marker == "_" else superscripted
        end = index + 1
        while end < len(raw) and end in active:
            end += 1
        if not result:
            decoder.unknown.add("ambiguous-leading-subscript")
            result.extend(decoder.char(char) for char in raw[index:end])
        else:
            result.append(f"{marker}{{{decoder.run(raw[index:end])}}}")
        index = end
    return "".join(result)


def decode_word(word: EquationWord, decoder: EquationDecoder) -> EquationWord:
    leading_superscripts = 0
    while leading_superscripts in word.superscript_indices:
        leading_superscripts += 1
    if leading_superscripts:
        base_match = FORMULA_PREFIX_RE.match(word.raw[leading_superscripts:])
        if base_match is not None:
            base_end = leading_superscripts + base_match.end()
            base_word = word_slice(word, leading_superscripts, base_end)
            script = decoder.run(word.raw[:leading_superscripts])
            base = decode_formula(
                base_word.raw, base_word.subscript_indices, decoder,
                base_word.superscript_indices,
            )
            return replace(
                word, text=f"[[formula:{{}}^{{{script}}}{base}]]{word.raw[base_end:]}",
            )
    parts: list[str] = []
    cursor = 0
    for match in PUA_RE.finditer(word.raw):
        if match.start() < cursor:
            continue
        end = match.end()
        while end < len(word.raw) and (
            end in word.subscript_indices or end in word.superscript_indices
        ):
            end += 1
        indices = tuple(
            index - match.start() for index in word.subscript_indices
            if match.start() <= index < end
        )
        superscripts = tuple(
            index - match.start() for index in word.superscript_indices
            if match.start() <= index < end
        )
        prefix = word.raw[cursor:match.start()]
        formula_raw = word.raw[match.start():end]
        if (
            prefix and prefix[-1].isascii() and prefix[-1].isalpha()
            and (0 in indices or 0 in superscripts)
        ):
            formula_raw, prefix = prefix[-1] + formula_raw, prefix[:-1]
            indices = tuple(index + 1 for index in indices)
            superscripts = tuple(index + 1 for index in superscripts)
        formula = decode_formula(formula_raw, indices, decoder, superscripts)
        parts.extend((prefix, f"[[formula:{formula}]]"))
        cursor = end
    parts.append(word.raw[cursor:])
    return replace(word, text="".join(parts))


def formula_prefix(
    raw: str,
    decoder: EquationDecoder,
    subscript_indices: tuple[int, ...] = (),
    superscript_indices: tuple[int, ...] = (),
) -> tuple[str, str]:
    leading_superscripts = 0
    superscripted = frozenset(superscript_indices)
    while leading_superscripts in superscripted:
        leading_superscripts += 1
    if leading_superscripts:
        base_match = FORMULA_PREFIX_RE.match(raw[leading_superscripts:])
        if base_match is not None:
            base_end = leading_superscripts + base_match.end()
            script = decoder.run(raw[:leading_superscripts])
            base = decode_formula(
                raw[leading_superscripts:base_end],
                tuple(
                    index - leading_superscripts for index in subscript_indices
                    if leading_superscripts <= index < base_end
                ),
                decoder,
                tuple(
                    index - leading_superscripts for index in superscript_indices
                    if leading_superscripts <= index < base_end
                ),
            )
            return f"{{}}^{{{script}}}{base}", raw[base_end:]
    match = FORMULA_PREFIX_RE.match(raw)
    if match is None:
        return "", raw
    indices = tuple(index for index in subscript_indices if index < match.end())
    superscripts = tuple(index for index in superscript_indices if index < match.end())
    return decode_formula(match.group(), indices, decoder, superscripts), raw[match.end():]


def formula_run(word: EquationWord, decoder: EquationDecoder) -> str:
    return formula_prefix(
        word.raw, decoder, word.subscript_indices, word.superscript_indices,
    )[0]


def word_slice(word: EquationWord, start: int, end: int | None = None) -> EquationWord:
    stop = len(word.raw) if end is None else end
    boxes = word.char_bboxes[start:stop] if word.char_bboxes else ()
    available = tuple(box for box in boxes if box is not None)
    bbox = (
        (
            min(box[0] for box in available), min(box[1] for box in available),
            max(box[2] for box in available), max(box[3] for box in available),
        ) if available else word.bbox
    )
    return replace(
        word, bbox=bbox, raw=word.raw[start:stop],
        subscript_indices=tuple(
            index - start for index in word.subscript_indices if start <= index < stop
        ),
        superscript_indices=tuple(
            index - start for index in word.superscript_indices if start <= index < stop
        ),
        char_bboxes=boxes,
    )


def split_structural_words(words: list[EquationWord]) -> list[EquationWord]:
    result: list[EquationWord] = []
    for word in words:
        if FRACTION_BAR not in word.raw or RADICAL_SIGN in word.raw:
            result.append(word)
            continue
        start = 0
        for index, char in enumerate(word.raw):
            if FORMULA_PREFIX_RE.fullmatch(char) is None:
                continue
            if start < index:
                result.append(word_slice(word, start, index))
            result.append(word_slice(word, index, index + 1))
            start = index + 1
        if start < len(word.raw):
            result.append(word_slice(word, start))
    return result


def bind_leading_superscripts(words: list[EquationWord]) -> list[EquationWord]:
    """Attach a separated charge only when one adjacent formula base is unique."""
    result: list[EquationWord] = []
    for word in words:
        run = 0
        while run in word.superscript_indices:
            run += 1
        remainder = word.raw[run:]
        same_word_base = bool(run and FORMULA_PREFIX_RE.match(remainder))
        allowed_tail = not remainder or remainder[0] in ",." or "가" <= remainder[0] <= "힣"
        previous = result[-1] if result else None
        prefix = word_slice(word, 0, run) if run else None
        if (
            run and not same_word_base and allowed_tail and previous is not None
            and FORMULA_PREFIX_RE.fullmatch(previous.raw) is not None
            and prefix is not None
            and 0 <= prefix.bbox[0] - previous.bbox[2] <= 4
            and abs(
                (prefix.bbox[1] + prefix.bbox[3] - previous.bbox[1] - previous.bbox[3]) / 2
            ) <= 8
        ):
            offset = len(previous.raw)
            merged_bbox = (
                previous.bbox[0], min(previous.bbox[1], prefix.bbox[1]),
                prefix.bbox[2], max(previous.bbox[3], prefix.bbox[3]),
            )
            result[-1] = replace(
                previous, bbox=merged_bbox, raw=previous.raw + prefix.raw,
                superscript_indices=(
                    *previous.superscript_indices,
                    *(offset + index for index in prefix.superscript_indices),
                ),
                char_bboxes=(*previous.char_bboxes, *prefix.char_bboxes),
            )
            if remainder:
                result.append(word_slice(word, run))
        else:
            result.append(word)
    return result
