from __future__ import annotations

from pathlib import Path

import pytest

from app.pdf_hwp_pipeline import build_editable_draft, detect_items
from app.pdf_hwp_pipeline_models import DraftArtifact


SOURCE = Path(r"C:\Users\user\Desktop\teach\시험문제\전체파일\e2_2023_11.pdf")


def _draft(item_number: int, output: Path) -> DraftArtifact:
    if not SOURCE.is_file():
        pytest.skip("missing e2_2023_11 source PDF")
    item = next(row for row in detect_items(SOURCE).items if row.item_number == item_number)
    return build_editable_draft(SOURCE, item, output)


@pytest.mark.parametrize("item_number", (7, 19))
def test_real_atmosphere_items_recover_phi(
    item_number: int, tmp_path: Path,
) -> None:
    draft = _draft(item_number, tmp_path / f"item-{item_number}")
    text = "\n".join((draft.source_text, *draft.choice_texts, draft.palette_markdown))

    assert r"sin[[formula:\phi=10^{-4}/]]s" in draft.source_text.replace(" ", "")
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in text)
