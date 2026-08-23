from __future__ import annotations

from pathlib import Path

from app.pdf_hwp_draft import _EquationDecoder, _Word, _choice_texts
from app.pdf_hwp_equation_font import EquationFontContext
from app.pdf_hwp_pipeline import LayoutStyle, build_editable_draft, detect_items


SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p1_2026_11.pdf")


def _word(x: float, y: float, text: str) -> _Word:
    return _Word((x, y, x + max(8.0, len(text) * 6.0), y + 11.0), text, text)


def test_text_choices_are_read_when_arranged_in_five_vertical_rows() -> None:
    # Given: five ordinary text choices arranged one per row.
    words = [
        _word(0, 0, "①"), _word(20, 0, "A"),
        _word(0, 18, "②"), _word(20, 18, "B"),
        _word(0, 36, "③"), _word(20, 36, "C"),
        _word(0, 54, "④"), _word(20, 54, "D"),
        _word(0, 72, "⑤"), _word(20, 72, "E"),
        _word(300, 104, "footer"),
    ]

    # When: the choice parser reads from the first marker.
    choices = _choice_texts(words, 0)

    # Then: all five rows are returned as text choices.
    assert choices == ("A", "B", "C", "D", "E")


def test_text_choices_are_read_when_arranged_in_two_columns() -> None:
    # Given: five formula choices arranged as a two-column, three-row grid.
    words = [
        _word(0, 0, "①"), _word(20, 0, "A"),
        _word(160, 0, "②"), _word(180, 0, "B"),
        _word(0, 30, "③"), _word(20, 30, "C"),
        _word(160, 30, "④"), _word(180, 30, "D"),
        _word(0, 60, "⑤"), _word(20, 60, "E"),
    ]

    # When: the choice parser reads the grid.
    choices = _choice_texts(words, 0)

    # Then: marker order, not PDF word order, determines the five choices.
    assert choices == ("A", "B", "C", "D", "E")


def test_text_choices_keep_content_joined_to_circled_markers() -> None:
    # Given: some PDFs encode the marker and first choice token as one word.
    words = [
        _word(0, 0, "①ㄱ"),
        _word(60, 0, "②ㄴ"),
        _word(120, 0, "③ㄷ"),
        _word(180, 0, "④ㄱ,"), _word(212, 0, "ㄷ"),
        _word(260, 0, "⑤ㄴ,"), _word(292, 0, "ㄷ"),
    ]

    assert _choice_texts(words, 0) == ("ㄱ", "ㄴ", "ㄷ", "ㄱ,ㄷ", "ㄴ,ㄷ")


def test_text_choices_keep_formula_glued_before_the_next_marker() -> None:
    words = [
        _word(0, 0, "①"), _word(20, 0, "A"),
        _word(160, 0, "②"), _word(180, 0, "B"),
        _word(320, 0, "③"), _word(340, 0, "C"),
        _word(0, 20, "④"),
        _word(20, 20, "[[formula:D]]⑤"),
        _word(160, 20, "E"),
    ]

    assert _choice_texts(words, 0) == ("A", "B", "C", "[[formula:D]]", "E")


def test_hyhwp_equation_font_decodes_all_corpus_symbols() -> None:
    # Given: every previously unregistered non-structural glyph in the corpus.
    raw = "\ue003\ue004\ue00d\ue00f\ue010\ue011\ue012\ue013\ue015\ue016\ue044\ue045\ue04f\ue052\ue055\ue056\ue099\ue0a4\ue0a7\ue0ad\ue0bb\ue0e5\ue0e7\ue0e9\ue0ea\ue0ef\ue0f4\ue0fb\ue101"

    # When: the verified HyhwpEQ decoder reads them.
    decoder = _EquationDecoder(EquationFontContext(
        frozenset(ord(char) for char in raw), (), (),
    ))
    decoded = decoder.run(raw)

    # Then: their actual installed-font symbols are preserved without unknowns.
    assert decoded == "DENPQRSTVW():,<>\\Phi\\theta\\lambda\\rho\\ellacefkpw|"
    assert decoder.unknown == set()


def test_real_vertical_and_grid_choice_items_build_as_editable_text(tmp_path: Path) -> None:
    # Given: real 2026-11 items 2 and 10 from the supplied corpus.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: both drafts are built through the production extraction seam.
    drafts = tuple(
        build_editable_draft(
            SOURCE, items[item_number], tmp_path, layout_style=LayoutStyle.SUNEUNG,
        )
        for item_number in (2, 10)
    )

    # Then: both use editable text choices rather than graphical-choice fallback.
    assert all(not draft.graphical_choice_assets for draft in drafts)
    choice_rows = tuple(
        tuple(filter(str.strip, draft.palette_markdown.splitlines()))[-5:]
        for draft in drafts
    )
    assert all(len(rows) == 5 and all(rows) for rows in choice_rows)


def test_real_overbar_subscripts_and_radical_build_as_editable_formulas(
    tmp_path: Path,
) -> None:
    # Given: real items containing an overbar, ASCII-base subscripts, and a radical.
    items = {item.item_number: item for item in detect_items(SOURCE).items}

    # When: the three drafts are built through production extraction.
    drafts = {
        item_number: build_editable_draft(
            SOURCE, items[item_number], tmp_path, layout_style=LayoutStyle.SUNEUNG,
        )
        for item_number in (6, 13, 15)
    }

    # Then: geometry is represented as machine-consumed formula structure.
    assert "\\bar{PQ}" in drafts[6].palette_markdown
    assert "D_{1}" in drafts[13].palette_markdown
    assert "I=4\\sqrt{2}I_{0}" in drafts[15].palette_markdown
    assert "I=4\\sqrt{2I_{0}}" not in drafts[15].palette_markdown
