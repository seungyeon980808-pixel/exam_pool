"""Coordinate-based radical, fraction, overbar, and row composition."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import assert_never

from .pdf_hwp_equation_decode import (
    bind_leading_subscripts, bind_leading_superscripts, decode_word, formula_prefix,
    formula_run, split_structural_words, word_slice,
)
from .pdf_hwp_equation_fraction import AmbiguousFractionNumerator, FractionNumerator, fraction_numerator
from .pdf_hwp_equation_glyphs import FRACTION_BAR, RADICAL_SIGN
from .pdf_hwp_equation_radical import bind_detached_inline_radicands, decode_inline_radical
from .pdf_hwp_equation_rows import group_rows as rows
from .pdf_hwp_equation_scripts import bind_detached_superscripts
from .pdf_hwp_equation_types import (
    EquationDecoder, EquationWord, FORMULA_PREFIX_RE, PUA_RE,
)
from .pdf_hwp_equation_vector import bind_vector_accents


def _bind_script_stacks(
    words: list[EquationWord], decoded: list[EquationWord], decoder: EquationDecoder,
) -> None:
    for upper_index, upper in enumerate(words):
        if (
            FRACTION_BAR in upper.raw
            or PUA_RE.match(upper.raw) is None
            or 0 in upper.superscript_indices
        ):
            continue
        upper_formula, tail = formula_prefix(
            upper.raw, decoder, upper.subscript_indices, upper.superscript_indices,
        )
        match = re.fullmatch(r"(\d+)([A-Za-z]+)", upper_formula)
        if match is None:
            continue
        upper_text, base = match.groups()
        lower_candidates = [
            index for index, lower in enumerate(words)
            if index != upper_index and FRACTION_BAR not in lower.raw
            and PUA_RE.fullmatch(lower.raw) is not None
            and abs(lower.bbox[0] - upper.bbox[0]) <= 0.75
            and 4 <= lower.bbox[1] - upper.bbox[1] <= 12
            and re.fullmatch(r"\d+", formula_run(lower, decoder))
        ]
        if len(lower_candidates) > 1:
            decoder.unknown.add(
                f"ambiguous-script-stack@{upper.bbox[0]:.2f},{upper.bbox[1]:.2f}"
            )
            continue
        if not lower_candidates:
            continue
        lower_index = lower_candidates[0]
        lower_text = formula_run(words[lower_index], decoder)
        decoded[upper_index] = replace(
            decoded[upper_index],
            text=f"[[formula:{{}}^{{{upper_text}}}_{{{lower_text}}}{base}]]{tail}",
        )
        decoded[lower_index] = replace(decoded[lower_index], suppressed=True)


def _bind_radicals(
    words: list[EquationWord], decoded: list[EquationWord], decoder: EquationDecoder,
) -> set[int]:
    consumed_bars: set[int] = set()
    for radical_index, radical in enumerate(words):
        if RADICAL_SIGN not in radical.raw or FRACTION_BAR in radical.raw:
            continue
        bars = [
            index for index, word in enumerate(words)
            if FRACTION_BAR in word.raw
            and (
                (
                    radical.bbox[2] - 1 <= word.bbox[0] <= radical.bbox[2] + 2
                    and abs(word.bbox[1] - radical.bbox[1]) <= 5
                )
                or (
                    word.bbox[0] <= radical.bbox[0]
                    and radical.bbox[2] <= word.bbox[2]
                    and 2 <= (
                        radical.bbox[1] + radical.bbox[3] - word.bbox[1] - word.bbox[3]
                    ) / 2 <= 12
                )
            )
        ]
        if len(bars) != 1:
            decoder.unknown.add(f"ambiguous-radical@{radical.bbox[0]:.2f},{radical.bbox[1]:.2f}")
            continue
        bar_index, bar = bars[0], words[bars[0]]
        radicands = [
            index for index, word in enumerate(words)
            if index not in {radical_index, bar_index}
            and bar.bbox[0] - 1 <= word.bbox[0] <= bar.bbox[2] + 1
            and word.bbox[0] >= radical.bbox[2] - 1
            and (
                2 <= word.bbox[1] - bar.bbox[1] <= 10
                or (
                    bar.bbox[0] < radical.bbox[0] - 1
                    and 2 <= (
                        word.bbox[1] + word.bbox[3] - bar.bbox[1] - bar.bbox[3]
                    ) / 2 <= 12
                )
            )
            and bool(formula_run(word, decoder))
        ]
        if len(radicands) != 1:
            decoder.unknown.add(f"ambiguous-radical@{radical.bbox[0]:.2f},{radical.bbox[1]:.2f}")
            continue
        radicand_index = radicands[0]
        formula, tail = formula_prefix(
            words[radicand_index].raw, decoder,
            words[radicand_index].subscript_indices,
            words[radicand_index].superscript_indices,
        )
        decoded[radical_index] = replace(
            decoded[radical_index], text=f"[[formula:\\sqrt{{{formula}}}]]{tail}",
        )
        owns_bar = bar.bbox[0] >= radical.bbox[0] - 1
        decoded[bar_index] = replace(decoded[bar_index], suppressed=owns_bar)
        decoded[radicand_index] = replace(decoded[radicand_index], suppressed=True)
        if owns_bar:
            consumed_bars.add(bar_index)
    return consumed_bars


_DECODED_FORMULA = re.compile(r"^\[\[formula:(.*)\]\](.*)$", re.DOTALL)
_EBS_COMPACT_FRACTION = re.compile(r"^;(?P<den>\d+)(?P<num>[!@#$%^&*()]);$")
_EBS_SHIFT_DIGIT = dict(zip("!@#$%^&*()", "1234567890", strict=True))


def _compact_fraction(word: EquationWord) -> EquationWord | None:
    match = _EBS_COMPACT_FRACTION.fullmatch(word.raw)
    if match is None:
        return None
    numerator = _EBS_SHIFT_DIGIT[match.group("num")]
    denominator = match.group("den")
    return replace(word, text=f"[[formula:\\frac{{{numerator}}}{{{denominator}}}]]")


def _bar_belongs_to_bound_radical(
    bar: EquationWord, words: list[EquationWord], decoded: list[EquationWord],
) -> bool:
    """True when a leftover bar is the packed viniculum of an already-bound radical."""
    return any(
        "\\sqrt" in item.text and not item.suppressed
        and abs(bar.bbox[0] - word.bbox[0]) <= 30
        and abs(bar.bbox[1] - word.bbox[1]) <= 24
        for word, item in zip(words, decoded, strict=True)
    )


def _numerator_formula(
    word: EquationWord, decoded: EquationWord, decoder: EquationDecoder,
) -> str | None:
    """Return a unique stacked-fraction numerator, including a bound radical."""
    if decoded.suppressed:
        return None
    bound = _DECODED_FORMULA.fullmatch(decoded.text)
    if bound is not None and RADICAL_SIGN in word.raw and bound.group(1):
        return bound.group(1)
    if FRACTION_BAR in word.raw or RADICAL_SIGN in word.raw:
        return None
    if FORMULA_PREFIX_RE.match(word.raw) is None:
        return None
    formula = formula_run(word, decoder)
    if not any(char.isalnum() or char == "\\" or "\u2160" <= char <= "\u216b" for char in formula):
        return None
    return formula


def _is_overbar_formula(formula: str) -> bool:
    return re.fullmatch(
        r"[A-Za-z](?:_\{[A-Za-z0-9]+\})?"
        r"(?:[A-Za-z](?:_\{[A-Za-z0-9]+\})?){0,3}",
        formula,
    ) is not None


def _clustered_bar_indices(
    bar_index: int, bar: EquationWord, words: list[EquationWord],
) -> list[int]:
    """Bars that share a header-underline row with this bar."""
    center = (bar.bbox[1] + bar.bbox[3]) / 2
    return [
        index for index, word in enumerate(words)
        if index != bar_index
        and FRACTION_BAR in word.raw
        and RADICAL_SIGN not in word.raw
        and abs((word.bbox[1] + word.bbox[3]) / 2 - center) <= 4
    ]


def normalize_equations(
    words: list[EquationWord], decoder: EquationDecoder,
) -> list[EquationWord]:
    words = bind_detached_inline_radicands(words)
    words = bind_detached_superscripts(
        bind_leading_subscripts(
            bind_leading_superscripts(split_structural_words(words)),
        ),
        decoder,
    )
    for word in words:
        if word.ambiguous_subscript:
            decoder.unknown.add(f"ambiguous-subscript@{word.bbox[0]:.2f},{word.bbox[1]:.2f}")
        if word.ambiguous_superscript:
            decoder.unknown.add(f"ambiguous-superscript@{word.bbox[0]:.2f},{word.bbox[1]:.2f}")
    decoded = [
        compact
        if (compact := _compact_fraction(word)) is not None
        else decode_inline_radical(word, decoder)
        if RADICAL_SIGN in word.raw and FRACTION_BAR in word.raw
        else decode_word(
            replace(word, raw=word.raw.replace(FRACTION_BAR, "").replace(RADICAL_SIGN, "")),
            decoder,
        ) if FRACTION_BAR in word.raw or RADICAL_SIGN in word.raw
        else decode_word(word, decoder)
        for word in words
    ]
    _bind_script_stacks(words, decoded, decoder)
    radical_bars = _bind_radicals(words, decoded, decoder)
    vector_bars = bind_vector_accents(words, decoded, decoder)
    consumed: set[int] = set()
    for bar_index, bar in enumerate(words):
        if (
            FRACTION_BAR not in bar.raw or RADICAL_SIGN in bar.raw
            or bar_index in radical_bars | vector_bars
        ):
            continue
        split_index = bar.raw.index(FRACTION_BAR)
        prefix = formula_run(word_slice(bar, 0, split_index), decoder)
        denominator = formula_run(word_slice(bar, split_index + 1), decoder)
        above = [
            index for index, word in enumerate(words)
            if index not in consumed | {bar_index}
            and _numerator_formula(word, decoded[index], decoder) is not None
            and bar.bbox[0] - 3 <= word.bbox[0] <= bar.bbox[2] + 3
            and 0 < (bar.bbox[1] + bar.bbox[3] - word.bbox[1] - word.bbox[3]) / 2 < 18
        ]
        overbar_below = [
            index for index, word in enumerate(words)
            if index not in consumed | {bar_index} and FRACTION_BAR not in word.raw
            and abs(word.bbox[0] - bar.bbox[0]) <= 1
            and 5 <= word.bbox[1] - bar.bbox[1] <= 12
        ]
        if len(overbar_below) == 1:
            index = overbar_below[0]
            overbar_formula, overbar_tail = formula_prefix(
                words[index].raw, decoder, words[index].subscript_indices,
                words[index].superscript_indices,
            )
            has_plain_above = any(
                formula_prefix(
                    words[candidate].raw, decoder, words[candidate].subscript_indices,
                    words[candidate].superscript_indices,
                )[1] == ""
                for candidate in above
            )
            if _is_overbar_formula(overbar_formula) and not has_plain_above:
                if overbar_tail:
                    overbar_tail = decode_word(
                        EquationWord(words[index].bbox, overbar_tail, overbar_tail),
                        decoder,
                    ).text
                decoded[index] = replace(
                    decoded[index], text=f"[[formula:\\bar{{{overbar_formula}}}]]{overbar_tail}",
                )
                decoded[bar_index] = replace(decoded[bar_index], suppressed=True)
                consumed.add(index)
                continue
        if not above:
            if _bar_belongs_to_bound_radical(bar, words, decoded):
                decoded[bar_index] = replace(decoded[bar_index], suppressed=True)
                continue
            decoder.unknown.add("U+E06D")
            continue
        above_index = min(above, key=lambda index: bar.bbox[1] - words[index].bbox[1])
        formula = _numerator_formula(words[above_index], decoded[above_index], decoder) or ""
        below_index = None
        extra_below: list[int] = []
        if not denominator:
            below = [
                index for index, word in enumerate(words)
                if index not in consumed | {bar_index, above_index}
                and FRACTION_BAR not in word.raw
                and _numerator_formula(word, decoded[index], decoder) is not None
                and bar.bbox[0] - 3 <= word.bbox[0] <= bar.bbox[2] + 3
                and 0 < (word.bbox[1] + word.bbox[3] - bar.bbox[1] - bar.bbox[3]) / 2 < 18
            ]
            if below:
                primary = min(below, key=lambda index: words[index].bbox[1])
                row = [
                    index for index in below
                    if abs(
                        (
                            words[index].bbox[1] + words[index].bbox[3]
                            - words[primary].bbox[1] - words[primary].bbox[3]
                        ) / 2
                    ) <= 4
                ]
                joined: list[tuple[int, str]] = []
                prev_right = None
                for index in sorted(row, key=lambda item: words[item].bbox[0]):
                    if prev_right is not None and words[index].bbox[0] - prev_right > 4:
                        break
                    piece = _numerator_formula(words[index], decoded[index], decoder) or ""
                    if not piece:
                        break
                    joined.append((index, piece))
                    prev_right = words[index].bbox[2]
                below_index = joined[0][0]
                extra_below = [index for index, _ in joined[1:]]
                denominator = "".join(piece for _, piece in joined)
                below_dy = (
                    words[below_index].bbox[1] + words[below_index].bbox[3]
                    - bar.bbox[1] - bar.bbox[3]
                ) / 2
                if (
                    _is_overbar_formula(formula)
                    and _clustered_bar_indices(bar_index, bar, words)
                    and below_dy > 8
                ):
                    below_index = None
                    extra_below = []
                    denominator = ""
        if not formula or not denominator:
            if _bar_belongs_to_bound_radical(bar, words, decoded):
                decoded[bar_index] = replace(decoded[bar_index], suppressed=True)
                continue
            if (
                formula
                and not denominator
                and _is_overbar_formula(formula)
                and _clustered_bar_indices(bar_index, bar, words)
            ):
                decoded[bar_index] = replace(decoded[bar_index], suppressed=True)
                continue
            decoder.unknown.add("U+E06D")
            continue
        if RADICAL_SIGN in words[above_index].raw:
            bound = _DECODED_FORMULA.fullmatch(decoded[above_index].text)
            numerator, suffix, tail = formula, "", bound.group(2) if bound else ""
        else:
            numerator_result = fraction_numerator(words[above_index], bar, decoder)
            match numerator_result:
                case AmbiguousFractionNumerator(x=x, y=y):
                    decoder.unknown.add(f"ambiguous-fraction-numerator@{x:.2f},{y:.2f}")
                    continue
                case FractionNumerator(numerator=numerator, suffix=suffix, tail=tail):
                    pass
                case unreachable:
                    assert_never(unreachable)
        composed = f"[[formula:{prefix}\\frac{{{numerator}}}{{{denominator}}}{suffix}]]{tail}"
        if RADICAL_SIGN in words[above_index].raw:
            decoded[bar_index] = replace(decoded[bar_index], text=composed, suppressed=False)
            decoded[above_index] = replace(decoded[above_index], suppressed=True)
        else:
            decoded[above_index] = replace(decoded[above_index], text=composed)
            decoded[bar_index] = replace(decoded[bar_index], suppressed=True)
        consumed.add(above_index)
        if below_index is not None:
            decoded[below_index] = replace(decoded[below_index], suppressed=True)
            consumed.add(below_index)
            for extra in extra_below:
                decoded[extra] = replace(decoded[extra], suppressed=True)
                consumed.add(extra)
    return decoded


def _join_formula_row(row: list[EquationWord]) -> str:
    parts: list[tuple[str, EquationWord]] = []
    for word in row:
        current = re.fullmatch(r"\[\[formula:(.*?)\]\](.*)", word.text)
        previous = re.fullmatch(r"\[\[formula:(.*)\]\]", parts[-1][0]) if parts else None
        if current is not None and previous is not None and word.bbox[0] - parts[-1][1].bbox[2] <= 8:
            prior = parts[-1][1]
            merged = f"[[formula:{previous.group(1)}{current.group(1)}]]{current.group(2)}"
            merged_bbox = (
                prior.bbox[0], min(prior.bbox[1], word.bbox[1]),
                word.bbox[2], max(prior.bbox[3], word.bbox[3]),
            )
            parts[-1] = (merged, replace(prior, bbox=merged_bbox, text=merged))
        else:
            parts.append((word.text, word))
    return " ".join(text for text, _ in parts).strip()


def join_rows(grouped: list[list[EquationWord]]) -> str:
    return " ".join(_join_formula_row(row) for row in grouped).strip()
