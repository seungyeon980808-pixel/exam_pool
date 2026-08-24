from __future__ import annotations

from app import pdf_hwp_draft as draft_extractor
from app.pdf_hwp_equation_font import EquationFontContext


def _decoder(*words: draft_extractor._Word) -> draft_extractor._EquationDecoder:
    return draft_extractor._EquationDecoder(EquationFontContext(
        frozenset(ord(char) for word in words for char in word.raw), (), (),
    ))


def test_fraction_with_missing_numerator_character_bbox_fails_closed() -> None:
    # Given: a stacked fraction whose second numerator glyph has no measured bounds.
    numerator = draft_extractor._Word(
        (100.0, 90.0, 112.0, 102.0), "\ue036\ue0fa", "\ue036\ue0fa",
        char_bboxes=((100.0, 90.0, 105.0, 102.0), None),
    )
    bar = draft_extractor._Word(
        (99.0, 100.0, 115.0, 112.0), "\ue06d", "\ue06d",
        char_bboxes=((99.0, 100.0, 115.0, 112.0),),
    )
    denominator = draft_extractor._Word(
        (102.0, 110.0, 113.0, 122.0), "\ue037\ue0eb", "\ue037\ue0eb",
        char_bboxes=(
            (102.0, 110.0, 107.0, 122.0),
            (108.0, 110.0, 113.0, 122.0),
        ),
    )
    decoder = _decoder(numerator, bar, denominator)

    # When: normalization cannot prove the full numerator extent.
    normalized = draft_extractor._normalize_fractions(
        [numerator, bar, denominator], decoder,
    )

    # Then: it records the measured origin and never assembles a fraction.
    assert decoder.unknown == {"ambiguous-fraction-numerator@100.00,90.00"}
    assert all("\\frac" not in word.text for word in normalized)


def test_fraction_with_zero_measured_numerator_overlap_fails_closed() -> None:
    # Given: every measured numerator center lies beyond the structural bar extent.
    numerator = draft_extractor._Word(
        (100.0, 90.0, 125.0, 102.0), "\ue036\ue0fa", "\ue036\ue0fa",
        char_bboxes=(
            (114.0, 90.0, 119.0, 102.0),
            (120.0, 90.0, 125.0, 102.0),
        ),
    )
    bar = draft_extractor._Word(
        (100.0, 100.0, 110.0, 112.0), "\ue06d", "\ue06d",
        char_bboxes=((100.0, 100.0, 110.0, 112.0),),
    )
    denominator = draft_extractor._Word(
        (102.0, 110.0, 110.0, 122.0), "\ue037\ue0eb", "\ue037\ue0eb",
        char_bboxes=(
            (102.0, 110.0, 106.0, 122.0),
            (106.0, 110.0, 110.0, 122.0),
        ),
    )
    decoder = _decoder(numerator, bar, denominator)

    # When: normalization cannot prove that any numerator glyph belongs over the bar.
    normalized = draft_extractor._normalize_fractions(
        [numerator, bar, denominator], decoder,
    )

    # Then: text fallback cannot invent a numerator from the unrelated word.
    assert decoder.unknown == {"ambiguous-fraction-numerator@100.00,90.00"}
    assert all("\\frac" not in word.text for word in normalized)


def test_fraction_tail_after_times_sign_decodes_remaining_pua() -> None:
    # Given: only the leading glyph sits over the bar; ×c=6 remains after the prefix.
    numerator = draft_extractor._Word(
        (100.0, 90.0, 108.0, 102.0), "\ue0e5×\ue0e7\ue047\ue039이다.",
        "\ue0e5×\ue0e7\ue047\ue039이다.",
        char_bboxes=(
            (100.0, 90.0, 108.0, 102.0),
            (108.0, 90.0, 116.0, 102.0),
            (116.0, 90.0, 124.0, 102.0),
            (124.0, 90.0, 132.0, 102.0),
            (132.0, 90.0, 140.0, 102.0),
            (140.0, 90.0, 148.0, 102.0),
            (148.0, 90.0, 156.0, 102.0),
            (156.0, 90.0, 164.0, 102.0),
        ),
    )
    bar = draft_extractor._Word(
        (99.0, 100.0, 110.0, 112.0), "\ue06d", "\ue06d",
        char_bboxes=((99.0, 100.0, 110.0, 112.0),),
    )
    denominator = draft_extractor._Word(
        (102.0, 116.0, 110.0, 128.0), "\ue0e6", "\ue0e6",
        char_bboxes=((102.0, 116.0, 110.0, 128.0),),
    )
    decoder = _decoder(numerator, bar, denominator)

    normalized = draft_extractor._normalize_fractions(
        [numerator, bar, denominator], decoder,
    )

    joined = "".join(word.text for word in normalized if not word.suppressed)
    assert r"\frac{a}{b}" in joined
    assert "[[formula:c=6]]" in joined
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in joined)
    assert decoder.unknown == set()


def test_plausible_detached_superscript_outside_safe_ratio_fails_closed() -> None:
    # Given: a detached digit with plausible stack geometry but a 0.75 height ratio.
    base = draft_extractor._Word(
        (100.0, 100.0, 113.0, 114.0), "\ue0fa\ue03d", "\ue0fa\ue03d",
        subscript_indices=(1,),
        char_bboxes=(
            (100.0, 100.0, 108.0, 112.0),
            (109.0, 106.0, 113.0, 114.0),
        ),
    )
    candidate = draft_extractor._Word(
        (109.0, 96.5, 113.0, 105.5), "\ue035", "\ue035",
        char_bboxes=((109.0, 96.5, 113.0, 105.5),),
    )
    decoder = _decoder(base, candidate)

    # When: normalization sees evidence inside the plausible 0.55-0.78 band only.
    normalized = draft_extractor._normalize_fractions([base, candidate], decoder)

    # Then: it does not silently demote the digit to ordinary inline text.
    assert decoder.unknown == {"ambiguous-superscript@109.00,96.50"}
    assert all("^{2}" not in word.text for word in normalized)


def test_prefixed_inline_radical_uses_bar_extent_and_keeps_prefix_scripts() -> None:
    # Given: x-squared prefixes a radical whose bar covers 5 but not the following v-zero.
    raw = "x\ue035\ue05c\ue06d\ue038\ue0fa\ue03d"
    word = draft_extractor._Word(
        (100.0, 94.0, 147.0, 114.0), raw, raw,
        superscript_indices=(1,), subscript_indices=(6,),
        char_bboxes=(
            (100.0, 100.0, 108.0, 112.0),
            (109.0, 95.0, 113.0, 103.0),
            (114.0, 100.0, 124.0, 112.0),
            (123.0, 94.0, 134.0, 106.0),
            (124.0, 100.0, 130.0, 112.0),
            (136.0, 100.0, 142.0, 112.0),
            (143.0, 106.0, 147.0, 114.0),
        ),
    )
    decoder = _decoder(word)

    # When: the inline radical is normalized.
    normalized = draft_extractor._normalize_fractions([word], decoder)

    # Then: the prefix script survives and only bar-covered glyphs enter the radicand.
    assert normalized[0].text == "[[formula:x^{2}\\sqrt{5}v_{0}]]"
    assert decoder.unknown == set()


def test_formula_overlapping_two_statement_markers_fails_closed() -> None:
    # Given: a tall formula intersects both adjacent statement-marker bands.
    first = draft_extractor._Word((100.0, 100.0, 112.0, 112.0), "ㄱ.", "ㄱ.")
    formula = draft_extractor._Word(
        (120.0, 108.0, 150.0, 124.0), "\ue008", "[[formula:I]]",
    )
    second = draft_extractor._Word((100.0, 118.0, 112.0, 130.0), "ㄴ.", "ㄴ.")
    decoder = _decoder(formula)

    # When: geometry cannot prove which source statement owns the formula.
    grouped = draft_extractor._rows([first, formula, second], decoder)

    # Then: no row move is invented and the draft boundary receives a manual-review reason.
    assert decoder.unknown == {"ambiguous-equation-row@120.00,108.00"}
    assert formula in grouped[0]


def test_formula_without_any_statement_marker_overlap_keeps_greedy_row() -> None:
    # Given: greedy y0 grouping attaches a formula that has no measured marker overlap.
    marker = draft_extractor._Word((100.0, 100.0, 112.0, 112.0), "ㄱ.", "ㄱ.")
    formula = draft_extractor._Word(
        (120.0, 114.0, 150.0, 116.0), "\ue008", "[[formula:I]]",
    )
    decoder = _decoder(formula)

    # When: no source statement marker can geometrically own the formula.
    grouped = draft_extractor._rows([marker, formula], decoder)

    # Then: zero reassignment evidence preserves the existing row without a new ambiguity.
    assert decoder.unknown == set()
    assert formula in grouped[0]
