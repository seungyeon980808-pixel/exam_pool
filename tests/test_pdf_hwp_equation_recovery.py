from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from pathlib import Path

import fitz
import pytest
from fontTools.ttLib import TTLibError

from app import pdf_hwp_draft as draft_extractor
from app import pdf_hwp_equation_font as equation_font
from app import pdf_hwp_font_view as font_view_cache
from app.pdf_hwp_equation_font import EquationFontContext, verify_equation_context
from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import UnsupportedDraftLayoutError


CORPUS = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일")


def _draft(paper: str, item_number: int, output: Path):
    source = CORPUS / f"{paper}.pdf"
    item = next(
        value for value in detect_items(source).items
        if value.item_number == item_number
    )
    return build_editable_draft(source, item, output)


def _all_text(draft) -> str:
    return "\n".join((draft.source_text, *draft.choice_texts, draft.palette_markdown))


def test_name_only_hyhwp_context_does_not_authorize_a_glyph() -> None:
    decoder = draft_extractor._EquationDecoder(frozenset({"HyhwpEQ"}))

    decoded = decoder.char("\ue053")

    assert decoded == "\ue053"
    assert decoder.unknown == {"U+E053"}


def test_real_verified_delta_mapping_becomes_editable_text(tmp_path: Path) -> None:
    draft = _draft("e1_2026_11", 18, tmp_path)

    combined = _all_text(draft)
    assert r"\Delta" in combined
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in combined)


def test_real_three_consecutive_fractions_keep_each_denominator(tmp_path: Path) -> None:
    draft = _draft("p1_2022_09", 6, tmp_path)

    combined = _all_text(draft).replace(" ", "")
    expected = (
        r"\frac{1}{\lambda_{a}}-\frac{1}{\lambda_{b}}="
        r"\frac{1}{\lambda_{c}}"
    )
    assert expected in combined
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in combined)


def test_real_standalone_radical_needs_no_prefix(tmp_path: Path) -> None:
    draft = _draft("p1_2020_11", 16, tmp_path)

    combined = _all_text(draft)
    assert r"\sqrt{3}" in combined
    assert "ambiguous-radical" not in combined


def test_real_fraction_preserves_denominator_superscript(tmp_path: Path) -> None:
    draft = _draft("p1_2022_06", 20, tmp_path)

    combined = _all_text(draft).replace(" ", "")
    assert r"\frac{12mgh}{d^{2}}" in combined
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in combined)


def test_real_fraction_binds_detached_numerator_superscript(tmp_path: Path) -> None:
    draft = _draft("p1_2025_09", 19, tmp_path)

    combined = _all_text(draft).replace(" ", "")
    assert r"\frac{3v_{0}^{2}}{4g}" in combined
    assert r"\frac{3v_{0}}{4g}2" not in combined


def test_real_inline_radical_stops_at_the_structural_bar_edge(tmp_path: Path) -> None:
    draft = _draft("p1_2025_09", 19, tmp_path)

    combined = _all_text(draft).replace(" ", "")
    assert r"\sqrt{5}v_{0}" in combined
    assert r"\sqrt{5v_{0}}" not in combined


def test_real_q15_root_equation_stays_owned_by_statement_nieum(tmp_path: Path) -> None:
    # Given: the radical equation overlaps the source ㄴ marker, not the preceding ㄱ text band.
    draft = _draft("p1_2026_11", 15, tmp_path)

    # When: source rows are serialized into the palette contract.
    expected_rows = (
        r"C에 흐르는 전류의 방향은 \수식{+x}방향이다."
        "\n"
        r"\수식{I=4\sqrt{2}I_{0}}이다."
    )

    # Then: ㄱ keeps its direction formula and ㄴ owns the radical equation.
    assert expected_rows in draft.palette_markdown
    assert r"C에 \수식{I=4\sqrt{2}I_{0}}" not in draft.palette_markdown


def test_real_q8_zero_overlap_equation_keeps_existing_row_ready(tmp_path: Path) -> None:
    # Given: q8 has a formula in a greedy marker row but no overlapping alternative marker.
    draft = _draft("p1_2026_11", 8, tmp_path)

    # Then: lack of reassignment evidence does not create a new manual-review reason.
    assert draft.warnings == ()
    assert "ambiguous-equation-row" not in _all_text(draft)


def test_real_q9_preserves_its_preexisting_manual_detail(tmp_path: Path) -> None:
    # Given: q9 already fails closed for an unrelated leading-subscript ambiguity.
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        _draft("p1_2020_11", 9, tmp_path)

    # Then: row reassignment contributes no speculative zero-overlap ambiguities.
    assert captured.value.detail == (
        "unverified equation glyphs require manual review: ambiguous-leading-subscript"
    )


def test_detached_superscript_with_two_compatible_bases_fails_closed() -> None:
    # Given: two formula words share the same trailing-subscript stack geometry.
    base = draft_extractor._Word(
        (100.0, 100.0, 113.0, 114.0), "\ue0fa\ue03d", "\ue0fa\ue03d",
        subscript_indices=(1,),
        char_bboxes=((100.0, 100.0, 108.0, 112.0), (109.0, 106.0, 113.0, 114.0)),
    )
    words = [
        base,
        base,
        draft_extractor._Word(
            (109.0, 97.0, 113.0, 105.0), "\ue035", "\ue035",
            char_bboxes=((109.0, 97.0, 113.0, 105.0),),
        ),
    ]
    decoder = draft_extractor._EquationDecoder(EquationFontContext(
        frozenset(ord(char) for word in words for char in word.raw), (), (),
    ))

    # When: normalization cannot identify exactly one base for the detached script.
    normalized = draft_extractor._normalize_fractions(words, decoder)

    # Then: no superscript is invented and the draft boundary receives an ambiguity.
    assert decoder.unknown == {"ambiguous-superscript@109.00,97.00"}
    assert all("^{" not in word.text for word in normalized)


def test_inline_radical_with_a_character_on_the_bar_edge_fails_closed() -> None:
    # Given: the first possible radicand center is within one point of the bar edge.
    raw = "\ue05c\ue06d\ue038"
    words = [draft_extractor._Word(
        (100.0, 96.0, 123.0, 114.0), raw, raw,
        char_bboxes=(
            (100.0, 100.0, 110.0, 112.0),
            (109.0, 96.0, 120.0, 108.0),
            (118.0, 102.0, 123.0, 114.0),
        ),
    )]
    decoder = draft_extractor._EquationDecoder(EquationFontContext(
        frozenset(ord(char) for char in raw), (), (),
    ))

    # When: normalization reaches the unresolved radical boundary.
    normalized = draft_extractor._normalize_fractions(words, decoder)

    # Then: the source stays literal and the enclosing draft must require review.
    assert decoder.unknown == {"ambiguous-radical@100.00,96.00"}
    assert normalized[0].text == raw


def test_existing_root_subscript_overbar_and_fraction_controls_stay_editable(
    tmp_path: Path,
) -> None:
    root = _draft("p1_2026_11", 15, tmp_path / "root")
    overbar = _draft("p1_2026_11", 6, tmp_path / "overbar")
    fraction = _draft("p1_2024_11", 16, tmp_path / "fraction")

    root_text = _all_text(root).replace(" ", "")
    assert r"I=4\sqrt{2}I_{0}" in root_text
    assert r"I=4\sqrt{2I_{0}}" not in root_text
    assert r"\bar{PQ}" in _all_text(overbar)
    assert r"h\frac{y_{0}}{v_{0}}" in _all_text(fraction).replace(" ", "")


def test_mixed_legacy_font_pua_never_escapes_as_a_ready_draft(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        _draft("c1_2026_06", 7, tmp_path)

    assert captured.value.detail == (
        "unverified equation glyphs require manual review: U+E039, U+E047, U+E0E7"
    )


def test_real_overbar_accepts_measured_subscripted_endpoints(tmp_path: Path) -> None:
    draft = _draft("p1_2025_09", 9, tmp_path)

    assert r"\bar{S_{1}S_{2}}" in _all_text(draft)


def test_real_fraction_accepts_ascii_numerator_and_denominator(tmp_path: Path) -> None:
    draft = _draft("c1_2025_11", 5, tmp_path)

    assert r"\frac{B}{A}" in _all_text(draft)


def test_real_leading_isotope_mass_numbers_bind_inside_the_same_word(tmp_path: Path) -> None:
    draft = _draft("c1_2027_06", 9, tmp_path)

    combined = _all_text(draft)
    assert r"{}^{2}H" in combined
    assert r"{}^{34}S" in combined


def test_real_separate_ionic_charge_binds_to_the_unique_previous_base(tmp_path: Path) -> None:
    draft = _draft("c1_2027_06", 19, tmp_path)

    combined = _all_text(draft)
    assert r"H^{+}" in combined
    assert r"A^{-}" in combined


def test_duplicate_hyhwp_resources_become_ready_with_exact_occurrence_xref(
    tmp_path: Path,
) -> None:
    draft = _draft("p1_2024_06", 3, tmp_path)

    assert not any(
        0xE000 <= ord(char) <= 0xF8FF for char in _all_text(draft)
    )


def test_duplicate_hyhwp_occurrences_bind_to_actual_xref_116() -> None:
    source = CORPUS / "p1_2024_06.pdf"
    item = next(value for value in detect_items(source).items if value.item_number == 3)

    with fitz.open(source) as document:
        context = verify_equation_context(document, document[0], item.bbox)

    assert context.evidence
    assert context.rejections == ()
    assert {entry.font_xref for entry in context.evidence} == {116}
    assert {entry.embedded_font_sha256 for entry in context.evidence} == {
        "93ee4e87b5c63b724b450b3dd257c5f3ed5534a0a6950a248e49aba388e1aed0"
    }
    assert all(entry.verified for entry in context.evidence)


def test_unavailable_low_level_font_trace_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(equation_font, "trace_font_glyphs", lambda _page: None)

    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        _draft("p1_2024_06", 3, tmp_path)

    assert "font-occurrence-xref-unavailable" in captured.value.detail


def test_malformed_actual_embedded_font_fails_with_explicit_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    source = CORPUS / "p1_2024_06.pdf"
    with fitz.open(source) as document:
        unreadable_sha = sha256(bytes(document.extract_font(116)[3])).hexdigest()
    real_font_view = equation_font._font_view

    def reject_actual_font(data: bytes):
        if sha256(data).hexdigest() == unreadable_sha:
            raise TTLibError("synthetic malformed embedded font")
        return real_font_view(data)

    monkeypatch.setattr(equation_font, "_font_view", reject_actual_font)

    item = next(value for value in detect_items(source).items if value.item_number == 3)
    with fitz.open(source) as document:
        context = verify_equation_context(document, document[0], item.bbox)

    assert context.rejections == ("embedded-font-unreadable:116",)
    assert {entry.reason for entry in context.evidence} == {
        "embedded-font-unreadable:116"
    }
    with pytest.raises(UnsupportedDraftLayoutError) as captured:
        _draft("p1_2024_06", 3, tmp_path)

    assert "embedded-font-unreadable:116" in captured.value.detail


def test_font_view_cache_is_bounded_lru_and_closes_evictions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []

    class FakeFontView:
        def __init__(self, data: bytes) -> None:
            self.data = data
            self.closed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

    cache = OrderedDict()
    monkeypatch.setattr(font_view_cache, "_FONT_CACHE", cache)
    monkeypatch.setattr(font_view_cache, "_FONT_CACHE_LIMIT", 2)
    monkeypatch.setattr(font_view_cache, "FontView", FakeFontView)

    first = font_view_cache.font_view(b"first")
    second = font_view_cache.font_view(b"second")
    assert font_view_cache.font_view(b"first") is first
    third = font_view_cache.font_view(b"third")

    assert len(cache) == 2
    assert second.closed
    assert not first.closed
    assert not third.closed


def test_real_fraction_superscript_and_radical_extent_follow_geometry(
    tmp_path: Path,
) -> None:
    draft = _draft("p1_2025_09", 19, tmp_path)

    combined = _all_text(draft).replace(" ", "")
    assert r"\frac{3v_{0}^{2}}{4g}" in combined
    assert r"\sqrt{5}v_{0}" in combined
    assert r"\frac{3v_{0}}{4g}2" not in combined
    assert r"\sqrt{5v_{0}}" not in combined
