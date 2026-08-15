from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from app.pdf_hwp_pipeline import (
    UnsupportedDraftLayoutError,
    build_editable_draft,
    detect_items,
)


SOURCE_ROOT = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일")


@pytest.mark.parametrize(
    ("source_name", "item_number"),
    (
        # Physics I baseline failures.
        ("p1_2021_11.pdf", 2),
        ("p1_2021_11.pdf", 5),
        ("p1_2022_06.pdf", 6),
        ("p1_2022_11.pdf", 2),
        ("p1_2023_11.pdf", 3),
        ("p1_2025_11.pdf", 2),
        ("p1_2026_09.pdf", 3),
        # Recent adjacent-subject failures from the requested corpus root.
        ("c1_2027_06.pdf", 11),
        ("c1_2027_06.pdf", 19),
        ("c1_2026_11.pdf", 10),
        ("c1_2026_09.pdf", 10),
        ("c1_2026_09.pdf", 16),
        ("c1_2026_09.pdf", 19),
        ("c2_2027_06.pdf", 6),
        ("c2_2027_06.pdf", 16),
        ("c2_2026_11.pdf", 9),
        ("c2_2026_09.pdf", 20),
        ("c2_2026_06.pdf", 10),
        ("b1_2027_06.pdf", 16),
        ("b1_2026_11.pdf", 2),
        ("b1_2026_09.pdf", 11),
        ("b1_2026_06.pdf", 7),
        ("b2_2027_06.pdf", 2),
        ("b2_2027_06.pdf", 9),
        ("b2_2026_11.pdf", 2),
        ("b2_2026_09.pdf", 2),
        ("b2_2026_09.pdf", 3),
        ("b2_2026_09.pdf", 5),
        ("b2_2025_11.pdf", 3),
        ("e1_2026_09.pdf", 11),
        ("e2_2026_06.pdf", 20),
    ),
)
def test_recent_item_never_emits_or_scales_an_empty_crop(
    tmp_path: Path,
    source_name: str,
    item_number: int,
) -> None:
    source = SOURCE_ROOT / source_name
    item = next(
        item for item in detect_items(source).items if item.item_number == item_number
    )

    try:
        draft = build_editable_draft(source, item, tmp_path / source.stem)
    except UnsupportedDraftLayoutError:
        # Safe uncertainty is an accepted result; low-level crop exceptions are not.
        return

    for artifact in (*draft.figure_assets, *draft.graphical_choice_assets):
        with Image.open(artifact.image_path) as image:
            assert image.width > 0
            assert image.height > 0
