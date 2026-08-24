"""Meaning and script decoding for verified equation words."""
from __future__ import annotations

from dataclasses import replace
import re

from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN, VECTOR_HEAD
from .pdf_hwp_equation_types import (
    EquationDecoder, EquationWord, FORMULA_PREFIX_RE, PUA_RE,
)

_CHARGE_TAIL = frozenset(",.→←↔⇄(")
_TRUSTED_EBS_FORMULA_PREFIX = re.compile(
    r"^(?:(?:\\[A-Za-z]+)|[A-Za-z0-9_{}+\-=/.*<>()])+(?=[^A-Za-z0-9_{}+\-=/.*<>()]|$)"
)
_EBS_COMPACT_FRACTION = re.compile(r"(?P<edge>[;:])(?P<body>[^;:\s]+)(?P=edge)")
_EBS_NUMERATOR_DIGIT = {
    **dict(zip("!@#$%^&*()", "1234567890", strict=True)),
    "Á": "1", "ª": "2", "£": "3", "¢": "4", "°": "5",
    "»": "9", "¼": "0",
}


def _expand_ebs_compact_fractions(raw: str) -> tuple[str, bool]:
    expanded, count = _EBS_COMPACT_FRACTION.subn(
        lambda match: rf"\frac{{{''.join(_EBS_NUMERATOR_DIGIT.get(char, '') for char in match.group('body'))}}}"
        rf"{{{''.join(char for char in match.group('body') if char.isascii() and char.isdigit())}}}",
        raw,
    )
    return expanded, count > 0


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
    ebs_raw, expanded_fraction = _expand_ebs_compact_fractions(word.raw)
    if word.trusted_formula or expanded_fraction:
        match = _TRUSTED_EBS_FORMULA_PREFIX.match(ebs_raw)
        if match is not None:
            return replace(
                word,
                text=f"[[formula:{match.group()}]]{ebs_raw[match.end():]}",
            )
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
    leading_subscripts = 0
    while leading_subscripts in word.subscript_indices:
        leading_subscripts += 1
    if leading_subscripts:
        base_match = FORMULA_PREFIX_RE.match(word.raw[leading_subscripts:])
        if base_match is not None:
            base_end = leading_subscripts + base_match.end()
            base_word = word_slice(word, leading_subscripts, base_end)
            script = decoder.run(word.raw[:leading_subscripts])
            base = decode_formula(
                base_word.raw, base_word.subscript_indices, decoder,
                base_word.superscript_indices,
            )
            return replace(
                word, text=f"[[formula:{{}}_{{{script}}}{base}]]{word.raw[base_end:]}",
            )
    internal_superscripts = tuple(
        index for index in word.superscript_indices if index > 0
    )
    superscript_runs = tuple(
        index for index in internal_superscripts
        if index - 1 not in word.superscript_indices
    )
    if len(superscript_runs) == 1:
        first = internal_superscripts[0]
        end = first + 1
        while end in word.superscript_indices:
            end += 1
        base_match = re.search(
            r"(?:(?:[A-Za-z0-9]+/)+[A-Za-z0-9]+|[A-Za-z0-9])$",
            word.raw[:first],
        )
        if base_match is not None:
            start = base_match.start()
            formula = decode_formula(
                word.raw[start:end],
                tuple(index - start for index in word.subscript_indices if start <= index < end),
                decoder,
                tuple(index - start for index in word.superscript_indices if start <= index < end),
            )
            prefix_text = decoder.run(word.raw[:start])
            if (
                prefix_text.endswith("/")
                and start
                and PUA_RE.fullmatch(word.raw[start - 1]) is not None
            ):
                prefix_text = prefix_text[:-1] + "[[formula:/]]"
            return replace(
                word,
                text=(
                    prefix_text
                    + f"[[formula:{formula}]]"
                    + decoder.run(word.raw[end:])
                ),
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
        packed = _split_raised_bar_from_radical(word)
        if packed is not None:
            result.extend(packed)
            continue
        if (
            FRACTION_BAR not in word.raw or RADICAL_SIGN in word.raw
            or word.raw == FRACTION_BAR + VECTOR_HEAD
        ):
            result.append(word)
            continue
        start = 0
        index = 0
        while index < len(word.raw):
            char = word.raw[index]
            if (
                char == FRACTION_BAR and index + 1 < len(word.raw)
                and word.raw[index + 1] == VECTOR_HEAD
            ):
                if start < index:
                    result.append(word_slice(word, start, index))
                result.append(word_slice(word, index, index + 2))
                index += 2
                start = index
                continue
            if FORMULA_PREFIX_RE.fullmatch(char) is None:
                index += 1
                continue
            if start < index:
                result.append(word_slice(word, start, index))
            result.append(word_slice(word, index, index + 1))
            start = index + 1
            index += 1
        if start < len(word.raw):
            result.append(word_slice(word, start))
    deduplicated: list[EquationWord] = []
    for word in result:
        duplicate_bar = word.raw == FRACTION_BAR and any(
            prior.raw == FRACTION_BAR
            and all(abs(left - right) <= 0.01 for left, right in zip(prior.bbox, word.bbox))
            for prior in deduplicated
        )
        if not duplicate_bar:
            deduplicated.append(word)
    return deduplicated


def bind_leading_superscripts(words: list[EquationWord]) -> list[EquationWord]:
    """Attach a separated charge only when one adjacent formula base is unique."""
    result: list[EquationWord] = []
    for word in words:
        run = 0
        while run in word.superscript_indices:
            run += 1
        remainder = word.raw[run:]
        same_word_base = bool(
            run and FORMULA_PREFIX_RE.match(remainder)
            and remainder[0] != "\ue04a"
        )
        allowed_tail = (
            not remainder
            or remainder[0] in _CHARGE_TAIL
            or remainder[0] == "\ue04a"
            or "가" <= remainder[0] <= "힣"
        )
        previous = result[-1] if result else None
        prefix = word_slice(word, 0, run) if run else None
        previous_is_base = (
            previous is not None
            and previous.raw
            and FORMULA_PREFIX_RE.fullmatch(previous.raw[-1]) is not None
        )
        if (
            run and not same_word_base and allowed_tail and previous_is_base
            and prefix is not None
            and 0 <= prefix.bbox[0] - previous.bbox[2] <= 4
            and abs(
                (prefix.bbox[1] + prefix.bbox[3] - previous.bbox[1] - previous.bbox[3]) / 2
            ) <= 8
        ):
            offset = len(previous.raw)
            closes_group = remainder == "\ue04a"
            attached = word if closes_group else prefix
            merged_bbox = (
                previous.bbox[0], min(previous.bbox[1], attached.bbox[1]),
                attached.bbox[2], max(previous.bbox[3], attached.bbox[3]),
            )
            result[-1] = replace(
                previous, bbox=merged_bbox, raw=previous.raw + attached.raw,
                superscript_indices=(
                    *previous.superscript_indices,
                    *(offset + index for index in prefix.superscript_indices),
                ),
                char_bboxes=(*previous.char_bboxes, *attached.char_bboxes),
            )
            if remainder and not closes_group:
                result.append(word_slice(word, run))
        else:
            result.append(word)
    return result


def _split_raised_bar_from_radical(word: EquationWord) -> list[EquationWord] | None:
    """Separate a fraction/overbar packed into the same PDF word as a radical sign."""
    if RADICAL_SIGN not in word.raw or FRACTION_BAR not in word.raw or not word.char_bboxes:
        return None
    radical_index = word.raw.index(RADICAL_SIGN)
    bar_index = word.raw.index(FRACTION_BAR)
    if bar_index != radical_index + 1:
        return None
    radical_box = word.char_bboxes[radical_index]
    bar_box = word.char_bboxes[bar_index]
    if radical_box is None or bar_box is None:
        return None
    bar_center = (bar_box[1] + bar_box[3]) / 2
    if bar_center >= radical_box[1] - 2:
        return None
    parts: list[EquationWord] = []
    if radical_index:
        parts.append(word_slice(word, 0, radical_index))
    parts.append(word_slice(word, radical_index, radical_index + 1))
    parts.append(word_slice(word, bar_index, bar_index + 1))
    if bar_index + 1 < len(word.raw):
        parts.append(word_slice(word, bar_index + 1))
    return parts


_OPERATOR_PUA = frozenset("\ue043\ue044\ue045\ue046\ue047\ue048\ue055\ue056")
_DIGIT_PUA = frozenset(chr(code) for code in range(0xE034, 0xE03E))


def bind_leading_subscripts(words: list[EquationWord]) -> list[EquationWord]:
    """Attach a split subscript digit to the preceding letter when geometry is unique."""
    result: list[EquationWord] = []
    for word in words:
        run = 0
        while run in word.subscript_indices:
            run += 1
        remainder = word.raw[run:]
        same_word_base = bool(run and FORMULA_PREFIX_RE.match(remainder))
        allowed_tail = not remainder or remainder[0] in ",." or "가" <= remainder[0] <= "힣"
        previous = result[-1] if result else None
        prefix = word_slice(word, 0, run) if run else None
        previous_is_letter = (
            previous is not None
            and previous.raw
            and previous.raw[-1] not in {FRACTION_BAR, RADICAL_SIGN, *_OPERATOR_PUA}
            and FORMULA_PREFIX_RE.fullmatch(previous.raw) is not None
        )
        subscript_is_digit = bool(prefix) and set(prefix.raw) <= _DIGIT_PUA
        if (
            run and not same_word_base and allowed_tail and prefix is not None
            and previous_is_letter and subscript_is_digit
            and -5 <= prefix.bbox[0] - previous.bbox[2] <= 3
            and 1.5 <= (prefix.bbox[1] + prefix.bbox[3] - previous.bbox[1] - previous.bbox[3]) / 2 <= 6.5
        ):
            offset = len(previous.raw)
            merged_bbox = (
                previous.bbox[0], min(previous.bbox[1], prefix.bbox[1]),
                max(previous.bbox[2], prefix.bbox[2]), max(previous.bbox[3], prefix.bbox[3]),
            )
            result[-1] = replace(
                previous, bbox=merged_bbox, raw=previous.raw + prefix.raw,
                subscript_indices=(
                    *previous.subscript_indices,
                    *(offset + index for index in prefix.subscript_indices),
                ),
                char_bboxes=(*previous.char_bboxes, *prefix.char_bboxes),
            )
            if remainder:
                result.append(word_slice(word, run))
        else:
            result.append(word)
    return result
