from __future__ import annotations

from pathlib import Path

import pytest

from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import DraftArtifact


SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\p2_2026_11.pdf")


def _draft(item_number: int, output: Path) -> DraftArtifact:
    if not SOURCE.is_file():
        pytest.skip("missing p2_2026_11 source PDF")
    item = next(row for row in detect_items(SOURCE).items if row.item_number == item_number)
    return build_editable_draft(SOURCE, item, output)


def _text(item_number: int, tmp_path: Path) -> str:
    draft = _draft(item_number, tmp_path / f"item-{item_number}")
    return "\n".join((draft.source_text, *draft.choice_texts, draft.palette_markdown)).replace(" ", "")


def test_real_q1_vector_heads_bind_to_force_symbols(tmp_path: Path) -> None:
    text = _text(1, tmp_path)

    assert r"\vec{F_{1}}" in text
    assert r"\vec{F_{2}}" in text
    assert r"\sqrt{6}" in text


def test_real_q7_epsilon_is_editable_in_stem_and_bogi(tmp_path: Path) -> None:
    text = _text(7, tmp_path)

    assert r"\varepsilon_{A}" in text
    assert r"\varepsilon_{B}" in text
    assert r"\varepsilon_{A}:\varepsilon_{B}=4:1" in text


def test_real_q15_pi_survives_all_five_fraction_choices(tmp_path: Path) -> None:
    text = _text(15, tmp_path)

    assert text.count(r"\pi") >= 5
    assert r"\frac{10B_{0}{\pi}d^{2}}{3t_{0}}" in text


def test_real_q17_nested_fraction_radical_uses_complete_denominator(tmp_path: Path) -> None:
    text = _text(17, tmp_path)

    assert r"\frac{1}{2\sqrt{3}}" in text
    assert r"\frac{7\sqrt{3}L}{20}" in text


def test_real_q18_detached_radicand_stays_inside_root(tmp_path: Path) -> None:
    text = _text(18, tmp_path)

    assert r"\sqrt{13}B_{0}" in text
    assert r"\sqrt{6}B_{0}" in text
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in text)
