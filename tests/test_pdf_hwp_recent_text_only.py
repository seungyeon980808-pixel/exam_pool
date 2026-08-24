from __future__ import annotations

from pathlib import Path

import pytest

from app.pdf_hwp_pipeline import build_editable_draft, detect_items


SOURCE_ROOT = Path.home() / "Desktop" / "teach" / "시험문제" / "전체파일"


@pytest.mark.parametrize(
    ("source_name", "item_number"),
    (("p1_2026_09.pdf", 3), ("p1_2025_11.pdf", 2)),
)
def test_text_only_item_builds_without_a_zero_width_figure(
    tmp_path: Path,
    source_name: str,
    item_number: int,
) -> None:
    # Given: a real text/formula-only item with no prompt photo or diagram.
    source = SOURCE_ROOT / source_name
    item = next(
        item for item in detect_items(source).items if item.item_number == item_number
    )

    # When: the production draft path reconstructs the item.
    draft = build_editable_draft(source, item, tmp_path / source.stem)

    # Then: it remains editable without inventing a zero-width figure asset.
    assert draft.palette_markdown.startswith("\\수능AI실제합답형\\")
    assert len(draft.choice_texts) == 5
    assert draft.figure_asset is None
    assert draft.figure_assets == ()
